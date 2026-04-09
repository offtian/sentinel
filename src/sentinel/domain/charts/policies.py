"""
Policy registry for the K8s chart coding agent.

Load team policies from YAML files in the ``policies/`` directory,
resolve user-to-team mappings, and merge a ``ChartSpec`` with policy
constraints -- detecting violations where the spec exceeds limits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from sentinel import settings as sentinel_settings
from sentinel.domain.charts import entities


_DEFAULT_POLICIES_DIR = sentinel_settings.PROJECT_ROOT / "policies"
_DEFAULT_TEAMS_FILE = _DEFAULT_POLICIES_DIR / "_teams.yaml"


def resolve_team(
    *,
    user_id: str,
    teams_file: Path = _DEFAULT_TEAMS_FILE,
) -> str:
    """
    Resolve a user ID to their team name.

    :param user_id: The user to look up.
    :param teams_file: Path to the _teams.yaml mapping file.
    :returns: The team name.
    :raises ValueError: if the user is not in the mapping.
    """
    with teams_file.open() as f:
        mapping: dict[str, str] = yaml.safe_load(f) or {}

    team = mapping.get(user_id)
    if team is None:
        msg = f"Unknown user: {user_id!r}. Add them to {teams_file}."
        raise ValueError(msg)
    return team


def load_team_policy(
    *,
    team: str,
    policies_dir: Path = _DEFAULT_POLICIES_DIR,
) -> entities.TeamPolicy:
    """
    Load a team's policy from its YAML file.

    :param team: Team name (matches ``<team>.yaml`` filename).
    :param policies_dir: Directory containing policy YAML files.
    :returns: The parsed TeamPolicy.
    :raises FileNotFoundError: if no policy file exists for the team.
    """
    policy_file = policies_dir / f"{team}.yaml"
    if not policy_file.exists():
        msg = f"No policy file for team {team!r} at {policy_file}"
        raise FileNotFoundError(msg)

    with policy_file.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # Convert allowed_egress dicts to EgressRule models
    egress_dicts: list[dict[str, Any]] = raw.pop("allowed_egress", [])
    egress_rules = tuple(entities.EgressRule(host=e["host"], port=e["port"]) for e in egress_dicts)

    return entities.TeamPolicy(**raw, allowed_egress=egress_rules)


def _parse_memory_to_bytes(value: str) -> int:
    """
    Convert a K8s memory string (e.g. ``"2Gi"``, ``"512Mi"``) to bytes.
    """
    value = value.strip()
    if not value:
        return 0
    if value.endswith("Gi"):
        return int(value[:-2]) * 1024 * 1024 * 1024
    if value.endswith("Mi"):
        return int(value[:-2]) * 1024 * 1024
    if value.endswith("Ki"):
        return int(value[:-2]) * 1024
    return int(value)


def _parse_cpu_to_millicores(value: str) -> int:
    """
    Convert a K8s CPU string (e.g. ``"2000m"``, ``"1.5"``) to millicores.
    """
    value = value.strip()
    if not value:
        return 0
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def merge_spec_with_policy(
    *,
    spec: entities.ChartSpec,
    policy: entities.TeamPolicy,
) -> tuple[entities.ChartSpec, tuple[entities.PolicyViolation, ...]]:
    """
    Merge a chart spec with team policy constraints.

    Apply policy defaults (non-root enforcement, NetworkPolicy injection)
    and detect violations where the spec exceeds policy limits.

    :param spec: The parsed chart specification.
    :param policy: The team's policy constraints.
    :returns: A tuple of (merged spec, violations found).
    """
    violations: list[entities.PolicyViolation] = []
    updates: dict[str, Any] = {}

    # Enforce non-root if policy requires it
    if policy.require_non_root and not spec.run_as_non_root:
        updates["run_as_non_root"] = True

    # Inject NetworkPolicy if required and not already requested
    if policy.require_network_policy and "NetworkPolicy" not in spec.extra_resources:
        updates["extra_resources"] = (*spec.extra_resources, "NetworkPolicy")

    # Check memory limit
    if spec.resources and spec.resources.memory_limit and policy.max_memory:
        requested_bytes = _parse_memory_to_bytes(spec.resources.memory_limit)
        allowed_bytes = _parse_memory_to_bytes(policy.max_memory)
        if requested_bytes > allowed_bytes:
            violations.append(
                entities.PolicyViolation(
                    field="memory_limit",
                    requested=spec.resources.memory_limit,
                    allowed=policy.max_memory,
                    message=(
                        f"Memory limit {spec.resources.memory_limit} "
                        f"exceeds team maximum of {policy.max_memory}"
                    ),
                )
            )

    # Check CPU limit
    if spec.resources and spec.resources.cpu_limit and policy.max_cpu:
        requested_mc = _parse_cpu_to_millicores(spec.resources.cpu_limit)
        allowed_mc = _parse_cpu_to_millicores(policy.max_cpu)
        if requested_mc > allowed_mc:
            violations.append(
                entities.PolicyViolation(
                    field="cpu_limit",
                    requested=spec.resources.cpu_limit,
                    allowed=policy.max_cpu,
                    message=(
                        f"CPU limit {spec.resources.cpu_limit} "
                        f"exceeds team maximum of {policy.max_cpu}"
                    ),
                )
            )

    # Check replica count
    if spec.replicas and policy.max_replicas > 0:
        if spec.replicas.max_replicas > policy.max_replicas:
            violations.append(
                entities.PolicyViolation(
                    field="max_replicas",
                    requested=str(spec.replicas.max_replicas),
                    allowed=str(policy.max_replicas),
                    message=(
                        f"Max replicas {spec.replicas.max_replicas} "
                        f"exceeds team maximum of {policy.max_replicas}"
                    ),
                )
            )

    merged = spec.model_copy(update=updates) if updates else spec
    return merged, tuple(violations)
