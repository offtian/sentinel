# K8s Chart Coding Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Pydantic Graph pipeline that takes natural language chart requests, applies team policies, generates Helm charts via PydanticAI agents, validates them, and commits to a GitOps directory.

**Architecture:** Pydantic Graph DAG (7 nodes) matching the SRE investigation pipeline pattern. Two PydanticAI agents (parser + generator). Policy registry as YAML files. Confidence scoring gates approval. Output to `gitops/charts/`.

**Tech Stack:** PydanticAI, Pydantic Graph, Pydantic Settings, Jinja2 (prompts), helm CLI, kubeconform, structlog

**Design spec:** `docs/plans/k8s-chart-coding-agent.md`

---

### Task 1: Settings

**Files:**
- Modify: `src/sentinel/settings.py`
- Test: `tests/unit/test_settings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/charts/test_settings.py`:

```python
from __future__ import annotations

from sentinel import settings


class TestChartSettings:
    def test_chart_settings_have_defaults(self) -> None:
        # Given the default settings

        # When settings are loaded
        cfg = settings.Settings()

        # Then chart-specific settings have sensible defaults
        assert cfg.k8s_chart_generator_llm == "openai/gpt-4.1"
        assert cfg.k8s_chart_parser_llm == "openai/gpt-4.1-mini"
        assert cfg.k8s_chart_auto_validate is False
        assert cfg.k8s_chart_auto_sandbox is False
        assert cfg.k8s_chart_sandbox_context == ""
        assert cfg.k8s_chart_max_retries == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/charts/test_settings.py -v`
Expected: FAIL — `Settings` has no attribute `k8s_chart_generator_llm`

- [ ] **Step 3: Add settings to SRESettings**

In `src/sentinel/settings.py`, add to the `SRESettings` class after the MCP settings block:

```python
    # K8s chart coding agent
    k8s_chart_generator_llm: str = "openai/gpt-4.1"
    k8s_chart_parser_llm: str = "openai/gpt-4.1-mini"
    k8s_chart_auto_validate: bool = False
    k8s_chart_auto_sandbox: bool = False
    k8s_chart_sandbox_context: str = ""
    k8s_chart_max_retries: int = 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/charts/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/settings.py tests/unit/charts/test_settings.py
git commit -m "feat: add K8s chart coding agent settings"
```

---

### Task 2: Domain Entities

**Files:**
- Create: `src/sentinel/domain/charts/__init__.py`
- Create: `src/sentinel/domain/charts/entities.py`
- Test: `tests/unit/domain/charts/test_entities.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/domain/charts/__init__.py` (empty) and `tests/unit/domain/charts/test_entities.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from sentinel.domain.charts import entities


class TestPortSpec:
    def test_stores_fields(self) -> None:
        # Given a port spec with known values
        port = entities.PortSpec(
            name="http", container_port=8000, service_port=80, protocol="TCP"
        )

        # Then fields are accessible
        assert port.name == "http"
        assert port.container_port == 8000
        assert port.service_port == 80
        assert port.protocol == "TCP"


class TestResourceSpec:
    def test_stores_fields(self) -> None:
        # Given a resource spec
        res = entities.ResourceSpec(
            cpu_request="100m", cpu_limit="500m",
            memory_request="128Mi", memory_limit="256Mi",
        )

        # Then fields are accessible
        assert res.cpu_request == "100m"
        assert res.memory_limit == "256Mi"


class TestChartRequest:
    def test_stores_fields(self) -> None:
        # Given a chart request
        now = datetime(2026, 4, 3, tzinfo=UTC)
        req = entities.ChartRequest(
            requester="U12345",
            team="trading-infra",
            raw_message="Deploy order-processor",
            requested_at=now,
        )

        # Then fields are accessible
        assert req.requester == "U12345"
        assert req.team == "trading-infra"


class TestChartSpec:
    def test_stores_fields(self) -> None:
        # Given a chart spec with all fields
        spec = entities.ChartSpec(
            service_name="order-processor",
            image="ghcr.io/acme/order-processor:latest",
            ports=[
                entities.PortSpec(name="http", container_port=8000, service_port=80, protocol="TCP"),
            ],
            resources=entities.ResourceSpec(
                cpu_request="100m", cpu_limit="500m",
                memory_request="128Mi", memory_limit="256Mi",
            ),
            replicas=entities.ReplicaSpec(min_replicas=2, max_replicas=10, target_cpu_percent=70),
            dependencies=[
                entities.DependencySpec(name="postgres", host="postgres.svc", port=5432),
            ],
            environment_variables=[
                entities.EnvVarSpec(name="DB_HOST", value="postgres.svc", secret_ref=None),
            ],
            run_as_non_root=True,
            extra_resources=[],
        )

        # Then fields are accessible
        assert spec.service_name == "order-processor"
        assert len(spec.ports) == 1
        assert spec.replicas.max_replicas == 10


class TestPolicyViolation:
    def test_stores_fields(self) -> None:
        # Given a policy violation
        violation = entities.PolicyViolation(
            field="resources.limits.memory",
            requested="2Gi",
            allowed="512Mi",
            message="Memory limit exceeds team cap",
        )

        # Then fields are accessible
        assert violation.field == "resources.limits.memory"
        assert violation.requested == "2Gi"


class TestChartOutput:
    def test_stores_fields(self) -> None:
        # Given a chart output with generated files
        output = entities.ChartOutput(
            service_name="order-processor",
            files=[
                entities.GeneratedFile(path="Chart.yaml", content="apiVersion: v2\n"),
            ],
            validation_result=None,
            policy_violations=[],
            generation_attempts=1,
            confidence_score=0.85,
        )

        # Then fields are accessible
        assert output.service_name == "order-processor"
        assert len(output.files) == 1
        assert output.confidence_score == 0.85


class TestValidationResult:
    def test_stores_fields(self) -> None:
        # Given a passing validation result
        result = entities.ValidationResult(
            helm_template_ok=True,
            kubeconform_ok=True,
            errors=[],
            warnings=["deprecated API version"],
        )

        # Then fields are accessible
        assert result.helm_template_ok is True
        assert len(result.warnings) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/charts/test_entities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sentinel.domain.charts'`

- [ ] **Step 3: Create domain entities**

Create `src/sentinel/domain/charts/__init__.py` (empty) and `src/sentinel/domain/charts/entities.py`:

```python
"""
Domain entities for the K8s chart coding agent.

These types flow through the chart generation pipeline:
ChartRequest → ChartSpec → ChartOutput.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PortSpec(BaseModel):
    name: str
    container_port: int
    service_port: int
    protocol: str  # "TCP" | "UDP"


class ResourceSpec(BaseModel):
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


class ReplicaSpec(BaseModel):
    min_replicas: int
    max_replicas: int
    target_cpu_percent: int


class DependencySpec(BaseModel):
    name: str
    host: str
    port: int


class EnvVarSpec(BaseModel):
    name: str
    value: str | None = None
    secret_ref: str | None = None


class EgressRule(BaseModel):
    name: str
    host: str
    port: int


class TeamPolicy(BaseModel):
    team: str
    namespace: str
    max_memory: str
    max_cpu: str
    max_replicas: int
    require_network_policy: bool
    require_non_root: bool
    allowed_egress: list[EgressRule]
    default_labels: dict[str, str]


class ChartRequest(BaseModel):
    requester: str
    team: str
    raw_message: str
    requested_at: datetime


class ChartSpec(BaseModel):
    service_name: str
    image: str
    ports: list[PortSpec]
    resources: ResourceSpec
    replicas: ReplicaSpec
    dependencies: list[DependencySpec]
    environment_variables: list[EnvVarSpec]
    run_as_non_root: bool
    extra_resources: list[str] = []


class GeneratedFile(BaseModel):
    path: str
    content: str


class PolicyViolation(BaseModel):
    field: str
    requested: str
    allowed: str
    message: str


class ValidationResult(BaseModel):
    helm_template_ok: bool
    kubeconform_ok: bool
    errors: list[str]
    warnings: list[str]


class ChartOutput(BaseModel):
    service_name: str
    files: list[GeneratedFile]
    validation_result: ValidationResult | None = None
    policy_violations: list[PolicyViolation] = []
    generation_attempts: int = 1
    confidence_score: float = 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/charts/test_entities.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/domain/charts/ tests/unit/domain/charts/
git commit -m "feat: add domain entities for chart coding agent"
```

---

### Task 3: Test Factories

**Files:**
- Modify: `tests/factories/__init__.py`
- Test: `tests/unit/domain/charts/test_entities.py` (already passing — this adds convenience factories)

- [ ] **Step 1: Add chart factories**

Append to `tests/factories/__init__.py`:

```python
from sentinel.domain.charts import entities as chart_entities


def make_chart_request(
    *,
    requester: str = "U12345",
    team: str = "internal-tooling",
    raw_message: str = "Deploy a FastAPI service called order-processor with Postgres",
    requested_at: datetime | None = None,
) -> chart_entities.ChartRequest:
    return chart_entities.ChartRequest(
        requester=requester,
        team=team,
        raw_message=raw_message,
        requested_at=requested_at or datetime(2026, 4, 3, tzinfo=UTC),
    )


def make_chart_spec(
    *,
    service_name: str = "order-processor",
    image: str = "ghcr.io/acme/order-processor:latest",
    ports: list[chart_entities.PortSpec] | None = None,
    resources: chart_entities.ResourceSpec | None = None,
    replicas: chart_entities.ReplicaSpec | None = None,
    dependencies: list[chart_entities.DependencySpec] | None = None,
    environment_variables: list[chart_entities.EnvVarSpec] | None = None,
    run_as_non_root: bool = True,
    extra_resources: list[str] | None = None,
) -> chart_entities.ChartSpec:
    return chart_entities.ChartSpec(
        service_name=service_name,
        image=image,
        ports=ports or [
            chart_entities.PortSpec(name="http", container_port=8000, service_port=80, protocol="TCP"),
        ],
        resources=resources or chart_entities.ResourceSpec(
            cpu_request="100m", cpu_limit="500m",
            memory_request="128Mi", memory_limit="256Mi",
        ),
        replicas=replicas or chart_entities.ReplicaSpec(
            min_replicas=2, max_replicas=10, target_cpu_percent=70,
        ),
        dependencies=dependencies or [
            chart_entities.DependencySpec(name="postgres", host="postgres.default.svc.cluster.local", port=5432),
        ],
        environment_variables=environment_variables or [
            chart_entities.EnvVarSpec(name="DB_HOST", value="postgres.default.svc.cluster.local"),
        ],
        run_as_non_root=run_as_non_root,
        extra_resources=extra_resources or [],
    )


def make_team_policy(
    *,
    team: str = "internal-tooling",
    namespace: str = "tools",
    max_memory: str = "2Gi",
    max_cpu: str = "2000m",
    max_replicas: int = 20,
    require_network_policy: bool = False,
    require_non_root: bool = False,
    allowed_egress: list[chart_entities.EgressRule] | None = None,
    default_labels: dict[str, str] | None = None,
) -> chart_entities.TeamPolicy:
    return chart_entities.TeamPolicy(
        team=team,
        namespace=namespace,
        max_memory=max_memory,
        max_cpu=max_cpu,
        max_replicas=max_replicas,
        require_network_policy=require_network_policy,
        require_non_root=require_non_root,
        allowed_egress=allowed_egress or [],
        default_labels=default_labels or {"tier": "internal"},
    )


def make_chart_output(
    *,
    service_name: str = "order-processor",
    files: list[chart_entities.GeneratedFile] | None = None,
    validation_result: chart_entities.ValidationResult | None = None,
    policy_violations: list[chart_entities.PolicyViolation] | None = None,
    generation_attempts: int = 1,
    confidence_score: float = 0.85,
) -> chart_entities.ChartOutput:
    return chart_entities.ChartOutput(
        service_name=service_name,
        files=files or [
            chart_entities.GeneratedFile(path="Chart.yaml", content="apiVersion: v2\nname: order-processor\n"),
            chart_entities.GeneratedFile(path="values.yaml", content="replicaCount: 2\n"),
        ],
        validation_result=validation_result,
        policy_violations=policy_violations or [],
        generation_attempts=generation_attempts,
        confidence_score=confidence_score,
    )
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `uv run pytest tests/unit/domain/charts/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/factories/__init__.py
git commit -m "feat: add chart entity test factories"
```

---

### Task 4: Policy Registry

**Files:**
- Create: `policies/_default.yaml`
- Create: `policies/trading-infra.yaml`
- Create: `policies/internal-tooling.yaml`
- Create: `policies/_teams.yaml`
- Create: `src/sentinel/domain/charts/policies.py`
- Test: `tests/unit/domain/charts/test_policies.py`

- [ ] **Step 1: Create policy YAML files**

Create `policies/_default.yaml`:

```yaml
team: _default
namespace: default
max_memory: "1Gi"
max_cpu: "1000m"
max_replicas: 10
require_network_policy: true
require_non_root: true
allowed_egress: []
default_labels:
  managed-by: sentinel-chart-agent
```

Create `policies/trading-infra.yaml`:

```yaml
team: trading-infra
namespace: trading
max_memory: "512Mi"
max_cpu: "500m"
max_replicas: 8
require_network_policy: true
require_non_root: true
allowed_egress:
  - name: postgres
    host: "postgres.trading.svc.cluster.local"
    port: 5432
  - name: redis
    host: "redis.trading.svc.cluster.local"
    port: 6379
default_labels:
  tier: critical
  compliance: sox
```

Create `policies/internal-tooling.yaml`:

```yaml
team: internal-tooling
namespace: tools
max_memory: "2Gi"
max_cpu: "2000m"
max_replicas: 20
require_network_policy: false
require_non_root: false
allowed_egress: []
default_labels:
  tier: internal
```

Create `policies/_teams.yaml`:

```yaml
users:
  U12345: trading-infra
  U67890: internal-tooling
default_team: internal-tooling
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/domain/charts/test_policies.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import yaml

from sentinel.domain.charts import entities, policies


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    """Create a temporary policy directory with test YAML files."""
    default_policy = {
        "team": "_default",
        "namespace": "default",
        "max_memory": "1Gi",
        "max_cpu": "1000m",
        "max_replicas": 10,
        "require_network_policy": True,
        "require_non_root": True,
        "allowed_egress": [],
        "default_labels": {"managed-by": "sentinel-chart-agent"},
    }
    trading_policy = {
        "team": "trading-infra",
        "namespace": "trading",
        "max_memory": "512Mi",
        "max_cpu": "500m",
        "max_replicas": 8,
        "require_network_policy": True,
        "require_non_root": True,
        "allowed_egress": [
            {"name": "postgres", "host": "postgres.trading.svc.cluster.local", "port": 5432},
        ],
        "default_labels": {"tier": "critical", "compliance": "sox"},
    }
    teams = {
        "users": {"U12345": "trading-infra", "U67890": "internal-tooling"},
        "default_team": "internal-tooling",
    }

    (tmp_path / "_default.yaml").write_text(yaml.dump(default_policy))
    (tmp_path / "trading-infra.yaml").write_text(yaml.dump(trading_policy))
    (tmp_path / "_teams.yaml").write_text(yaml.dump(teams))
    return tmp_path


class TestLoadTeamPolicy:
    def test_loads_existing_team_policy(self, policy_dir: Path) -> None:
        # Given a policy directory with a trading-infra policy

        # When loading the policy for trading-infra
        policy = policies.load_team_policy(team="trading-infra", policies_dir=policy_dir)

        # Then the correct policy is returned
        assert policy.team == "trading-infra"
        assert policy.namespace == "trading"
        assert policy.max_memory == "512Mi"
        assert policy.require_non_root is True
        assert len(policy.allowed_egress) == 1
        assert policy.allowed_egress[0].name == "postgres"

    def test_falls_back_to_default_policy(self, policy_dir: Path) -> None:
        # Given a policy directory with no policy for "unknown-team"

        # When loading the policy for an unknown team
        policy = policies.load_team_policy(team="unknown-team", policies_dir=policy_dir)

        # Then the default policy is returned
        assert policy.team == "_default"
        assert policy.max_memory == "1Gi"

    def test_raises_when_no_default_exists(self, tmp_path: Path) -> None:
        # Given an empty policy directory

        # When loading a policy for any team
        # Then a FileNotFoundError is raised
        with pytest.raises(FileNotFoundError, match="No policy file found"):
            policies.load_team_policy(team="anything", policies_dir=tmp_path)


class TestResolveTeam:
    def test_resolves_known_user(self, policy_dir: Path) -> None:
        # Given a teams mapping with U12345 → trading-infra

        # When resolving team for U12345
        team = policies.resolve_team(user_id="U12345", policies_dir=policy_dir)

        # Then the correct team is returned
        assert team == "trading-infra"

    def test_resolves_unknown_user_to_default(self, policy_dir: Path) -> None:
        # Given a teams mapping with a default_team of internal-tooling

        # When resolving team for an unknown user
        team = policies.resolve_team(user_id="UUNKNOWN", policies_dir=policy_dir)

        # Then the default team is returned
        assert team == "internal-tooling"


class TestMergeSpecWithPolicy:
    def test_enforces_non_root_from_policy(self) -> None:
        # Given a spec that allows root and a policy that requires non-root
        spec = entities.ChartSpec(
            service_name="test",
            image="test:latest",
            ports=[],
            resources=entities.ResourceSpec(
                cpu_request="100m", cpu_limit="500m",
                memory_request="128Mi", memory_limit="256Mi",
            ),
            replicas=entities.ReplicaSpec(min_replicas=1, max_replicas=5, target_cpu_percent=70),
            dependencies=[],
            environment_variables=[],
            run_as_non_root=False,
        )
        policy = entities.TeamPolicy(
            team="strict",
            namespace="prod",
            max_memory="1Gi",
            max_cpu="1000m",
            max_replicas=10,
            require_network_policy=False,
            require_non_root=True,
            allowed_egress=[],
            default_labels={},
        )

        # When merging spec with policy
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then non-root is enforced and no violations are raised
        assert merged.run_as_non_root is True
        assert len(violations) == 0

    def test_detects_memory_limit_violation(self) -> None:
        # Given a spec requesting 2Gi and a policy capping at 512Mi
        spec = entities.ChartSpec(
            service_name="test",
            image="test:latest",
            ports=[],
            resources=entities.ResourceSpec(
                cpu_request="100m", cpu_limit="500m",
                memory_request="128Mi", memory_limit="2Gi",
            ),
            replicas=entities.ReplicaSpec(min_replicas=1, max_replicas=5, target_cpu_percent=70),
            dependencies=[],
            environment_variables=[],
            run_as_non_root=True,
        )
        policy = entities.TeamPolicy(
            team="strict",
            namespace="prod",
            max_memory="512Mi",
            max_cpu="1000m",
            max_replicas=10,
            require_network_policy=False,
            require_non_root=False,
            allowed_egress=[],
            default_labels={},
        )

        # When merging spec with policy
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then a memory violation is detected
        assert len(violations) == 1
        assert violations[0].field == "resources.limits.memory"
        assert violations[0].requested == "2Gi"
        assert violations[0].allowed == "512Mi"

    def test_detects_replica_limit_violation(self) -> None:
        # Given a spec requesting max 50 replicas and a policy capping at 8
        spec = entities.ChartSpec(
            service_name="test",
            image="test:latest",
            ports=[],
            resources=entities.ResourceSpec(
                cpu_request="100m", cpu_limit="500m",
                memory_request="128Mi", memory_limit="256Mi",
            ),
            replicas=entities.ReplicaSpec(min_replicas=2, max_replicas=50, target_cpu_percent=70),
            dependencies=[],
            environment_variables=[],
            run_as_non_root=True,
        )
        policy = entities.TeamPolicy(
            team="strict",
            namespace="prod",
            max_memory="1Gi",
            max_cpu="1000m",
            max_replicas=8,
            require_network_policy=False,
            require_non_root=False,
            allowed_egress=[],
            default_labels={},
        )

        # When merging spec with policy
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then a replica violation is detected
        assert len(violations) == 1
        assert violations[0].field == "replicas.max_replicas"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/charts/test_policies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sentinel.domain.charts.policies'`

- [ ] **Step 4: Implement policy loader**

Create `src/sentinel/domain/charts/policies.py`:

```python
"""
Policy registry for the K8s chart coding agent.

Loads team policies from YAML files and merges them with chart specs
to enforce organisational constraints.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from sentinel.domain.charts import entities
from sentinel.settings import PROJECT_ROOT


_DEFAULT_POLICIES_DIR = PROJECT_ROOT / "policies"

# Kubernetes resource units in bytes for comparison
_MEMORY_UNITS: dict[str, int] = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
}


def _parse_memory_bytes(value: str) -> int:
    """
    Parse a Kubernetes memory string (e.g. '512Mi') into bytes.

    :raises ValueError: if the format is unrecognised.
    """
    match = re.match(r"^(\d+)(Ki|Mi|Gi|Ti)$", value)
    if not match:
        msg = f"Unrecognised memory format: {value}"
        raise ValueError(msg)
    return int(match.group(1)) * _MEMORY_UNITS[match.group(2)]


def load_team_policy(
    *,
    team: str,
    policies_dir: Path = _DEFAULT_POLICIES_DIR,
) -> entities.TeamPolicy:
    """
    Load the policy for a given team.

    Falls back to ``_default.yaml`` if no team-specific file exists.

    :param team: team identifier (e.g. ``"trading-infra"``)
    :param policies_dir: directory containing policy YAML files
    :raises FileNotFoundError: if neither team policy nor default policy exists
    """
    team_file = policies_dir / f"{team}.yaml"
    default_file = policies_dir / "_default.yaml"

    policy_file = team_file if team_file.exists() else default_file

    if not policy_file.exists():
        msg = f"No policy file found for team '{team}' and no _default.yaml in {policies_dir}"
        raise FileNotFoundError(msg)

    raw = yaml.safe_load(policy_file.read_text())
    return entities.TeamPolicy(**raw)


def resolve_team(
    *,
    user_id: str,
    policies_dir: Path = _DEFAULT_POLICIES_DIR,
) -> str:
    """
    Resolve a user ID to a team name via the ``_teams.yaml`` mapping.

    :param user_id: Slack user ID or Streamlit session ID
    :param policies_dir: directory containing ``_teams.yaml``
    """
    teams_file = policies_dir / "_teams.yaml"
    if not teams_file.exists():
        return "internal-tooling"

    raw = yaml.safe_load(teams_file.read_text())
    users = raw.get("users", {})
    default_team = raw.get("default_team", "internal-tooling")
    return users.get(user_id, default_team)


def merge_spec_with_policy(
    *,
    spec: entities.ChartSpec,
    policy: entities.TeamPolicy,
) -> tuple[entities.ChartSpec, list[entities.PolicyViolation]]:
    """
    Merge a chart spec with a team policy.

    Auto-resolves fixable issues (e.g. enforcing non-root).
    Returns policy violations for business conflicts that need human review.

    :param spec: the chart spec extracted from the user's request
    :param policy: the team's policy constraints
    """
    violations: list[entities.PolicyViolation] = []
    updates: dict[str, object] = {}

    # Enforce non-root if policy requires it
    if policy.require_non_root and not spec.run_as_non_root:
        updates["run_as_non_root"] = True

    # Check memory limit
    try:
        requested_memory = _parse_memory_bytes(spec.resources.memory_limit)
        allowed_memory = _parse_memory_bytes(policy.max_memory)
        if requested_memory > allowed_memory:
            violations.append(
                entities.PolicyViolation(
                    field="resources.limits.memory",
                    requested=spec.resources.memory_limit,
                    allowed=policy.max_memory,
                    message=f"Requested memory {spec.resources.memory_limit} exceeds team cap of {policy.max_memory}",
                )
            )
    except ValueError:
        pass  # Non-standard format — skip check

    # Check CPU limit
    try:
        requested_cpu = int(spec.resources.cpu_limit.rstrip("m"))
        allowed_cpu = int(policy.max_cpu.rstrip("m"))
        if requested_cpu > allowed_cpu:
            violations.append(
                entities.PolicyViolation(
                    field="resources.limits.cpu",
                    requested=spec.resources.cpu_limit,
                    allowed=policy.max_cpu,
                    message=f"Requested CPU {spec.resources.cpu_limit} exceeds team cap of {policy.max_cpu}",
                )
            )
    except ValueError:
        pass

    # Check replica limit
    if spec.replicas.max_replicas > policy.max_replicas:
        violations.append(
            entities.PolicyViolation(
                field="replicas.max_replicas",
                requested=str(spec.replicas.max_replicas),
                allowed=str(policy.max_replicas),
                message=f"Requested max replicas {spec.replicas.max_replicas} exceeds team cap of {policy.max_replicas}",
            )
        )

    merged = spec.model_copy(update=updates) if updates else spec
    return merged, violations
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/charts/test_policies.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 6: Commit**

```bash
git add policies/ src/sentinel/domain/charts/policies.py tests/unit/domain/charts/test_policies.py
git commit -m "feat: add policy registry and merge logic for chart agent"
```

---

### Task 5: Confidence Scoring

**Files:**
- Create: `src/sentinel/domain/charts/confidence.py`
- Test: `tests/unit/domain/charts/test_confidence.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/domain/charts/test_confidence.py`:

```python
from __future__ import annotations

from sentinel.domain.charts import confidence


class TestCalculateChartConfidence:
    def test_perfect_score(self) -> None:
        # Given a chart that passed all checks with no retries
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_violation_count=0,
            auto_resolved_count=0,
            requested_resource_count=4,
            generated_resource_count=4,
            retry_count=0,
        )

        # Then the confidence score is 1.0
        assert score == 1.0

    def test_schema_failure_drops_score(self) -> None:
        # Given a chart that failed schema validation
        score = confidence.calculate_chart_confidence(
            schema_valid=False,
            template_renders=True,
            template_has_warnings=False,
            policy_violation_count=0,
            auto_resolved_count=0,
            requested_resource_count=4,
            generated_resource_count=4,
            retry_count=0,
        )

        # Then the score drops by the schema weight (0.3)
        assert score == pytest.approx(0.7, abs=0.01)

    def test_retries_degrade_score(self) -> None:
        # Given a chart that took 2 retries
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_violation_count=0,
            auto_resolved_count=0,
            requested_resource_count=4,
            generated_resource_count=4,
            retry_count=2,
        )

        # Then the score reflects retry degradation (0.1 weight * 0.4 for 2 retries)
        assert score < 1.0
        assert score == pytest.approx(0.94, abs=0.01)

    def test_partial_coverage_reduces_score(self) -> None:
        # Given a chart that generated 2 of 4 requested resources
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_violation_count=0,
            auto_resolved_count=0,
            requested_resource_count=4,
            generated_resource_count=2,
            retry_count=0,
        )

        # Then coverage factor reduces the score
        assert score == pytest.approx(0.925, abs=0.01)

    def test_policy_violations_zero_compliance(self) -> None:
        # Given a chart with unresolved policy violations
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_violation_count=2,
            auto_resolved_count=0,
            requested_resource_count=4,
            generated_resource_count=4,
            retry_count=0,
        )

        # Then compliance factor is 0.0 (0.25 weight lost)
        assert score == pytest.approx(0.75, abs=0.01)
```

Add `import pytest` to the imports at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/charts/test_confidence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sentinel.domain.charts.confidence'`

- [ ] **Step 3: Implement confidence scoring**

Create `src/sentinel/domain/charts/confidence.py`:

```python
"""
Weighted confidence scoring for chart generation quality.

Factors: schema validity (0.3), template rendering (0.2),
policy compliance (0.25), spec coverage (0.15), retry count (0.1).
"""

from __future__ import annotations

_WEIGHT_SCHEMA = 0.3
_WEIGHT_TEMPLATE = 0.2
_WEIGHT_COMPLIANCE = 0.25
_WEIGHT_COVERAGE = 0.15
_WEIGHT_RETRIES = 0.1

_RETRY_SCORES = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.1}


def calculate_chart_confidence(
    *,
    schema_valid: bool,
    template_renders: bool,
    template_has_warnings: bool,
    policy_violation_count: int,
    auto_resolved_count: int,
    requested_resource_count: int,
    generated_resource_count: int,
    retry_count: int,
) -> float:
    """
    Calculate a weighted confidence score for a generated chart.

    :param schema_valid: whether kubeconform passed
    :param template_renders: whether helm template succeeded
    :param template_has_warnings: whether helm template produced warnings
    :param policy_violation_count: number of unresolved policy violations
    :param auto_resolved_count: number of auto-resolved policy issues
    :param requested_resource_count: how many K8s resources were requested
    :param generated_resource_count: how many K8s resources were generated
    :param retry_count: number of self-heal retries (0-3)
    """
    schema_score = 1.0 if schema_valid else 0.0

    if template_renders and not template_has_warnings:
        template_score = 1.0
    elif template_renders:
        template_score = 0.5
    else:
        template_score = 0.0

    if policy_violation_count == 0 and auto_resolved_count == 0:
        compliance_score = 1.0
    elif policy_violation_count == 0:
        compliance_score = 0.7
    else:
        compliance_score = 0.0

    if requested_resource_count > 0:
        coverage_score = min(generated_resource_count / requested_resource_count, 1.0)
    else:
        coverage_score = 1.0

    retry_score = _RETRY_SCORES.get(retry_count, 0.1)

    total = (
        schema_score * _WEIGHT_SCHEMA
        + template_score * _WEIGHT_TEMPLATE
        + compliance_score * _WEIGHT_COMPLIANCE
        + coverage_score * _WEIGHT_COVERAGE
        + retry_score * _WEIGHT_RETRIES
    )
    return round(total, 4)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/charts/test_confidence.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/domain/charts/confidence.py tests/unit/domain/charts/test_confidence.py
git commit -m "feat: add weighted confidence scoring for chart generation"
```

---

### Task 6: Validation Runner

**Files:**
- Create: `src/sentinel/domain/charts/validation.py`
- Test: `tests/unit/domain/charts/test_validation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/domain/charts/test_validation.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from sentinel.domain.charts import entities, validation


class TestWriteChartToDir:
    def test_writes_files_to_directory(self, tmp_path: Path) -> None:
        # Given a chart output with two files
        output = entities.ChartOutput(
            service_name="my-app",
            files=[
                entities.GeneratedFile(path="Chart.yaml", content="apiVersion: v2\n"),
                entities.GeneratedFile(path="templates/deployment.yaml", content="kind: Deployment\n"),
            ],
            generation_attempts=1,
            confidence_score=0.9,
        )

        # When writing to a directory
        chart_dir = validation.write_chart_to_dir(output=output, base_dir=tmp_path)

        # Then files are written at the correct paths
        assert (chart_dir / "Chart.yaml").read_text() == "apiVersion: v2\n"
        assert (chart_dir / "templates" / "deployment.yaml").read_text() == "kind: Deployment\n"


class TestRunHelmTemplate:
    def test_returns_success_on_valid_chart(self, tmp_path: Path) -> None:
        # Given a valid Chart.yaml and templates directory
        chart_dir = tmp_path / "test-chart"
        chart_dir.mkdir()
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2\nname: test\nversion: 0.1.0\n")
        templates_dir = chart_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "configmap.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test\ndata: {}\n"
        )

        # When running helm template
        result = validation.run_helm_template(chart_dir=chart_dir)

        # Then it succeeds
        assert result.helm_template_ok is True
        assert len(result.errors) == 0

    @mock.patch("sentinel.domain.charts.validation.subprocess")
    def test_returns_failure_on_invalid_chart(self, mock_subprocess: mock.Mock) -> None:
        # Given helm template returns an error
        mock_subprocess.run.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Error: chart metadata (Chart.yaml) missing",
        )

        # When running helm template
        result = validation.run_helm_template(chart_dir=Path("/fake"))

        # Then it reports failure
        assert result.helm_template_ok is False
        assert len(result.errors) == 1
        assert "Chart.yaml" in result.errors[0]


class TestRunKubeconform:
    @mock.patch("sentinel.domain.charts.validation.subprocess")
    def test_returns_success_on_valid_manifests(self, mock_subprocess: mock.Mock) -> None:
        # Given kubeconform returns success
        mock_subprocess.run.return_value = mock.Mock(
            returncode=0, stdout="", stderr=""
        )

        # When running kubeconform
        result = validation.run_kubeconform(rendered_yaml="apiVersion: v1\nkind: ConfigMap\n")

        # Then it succeeds
        assert result.kubeconform_ok is True

    @mock.patch("sentinel.domain.charts.validation.subprocess")
    def test_returns_failure_on_invalid_manifests(self, mock_subprocess: mock.Mock) -> None:
        # Given kubeconform returns errors
        mock_subprocess.run.return_value = mock.Mock(
            returncode=1,
            stdout="stdin - ConfigMap test is invalid: missing 'metadata.name'",
            stderr="",
        )

        # When running kubeconform
        result = validation.run_kubeconform(rendered_yaml="kind: ConfigMap\n")

        # Then it reports failure
        assert result.kubeconform_ok is False
        assert len(result.errors) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/charts/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement validation runner**

Create `src/sentinel/domain/charts/validation.py`:

```python
"""
Chart validation using helm and kubeconform CLIs.

Writes generated chart files to a temporary directory, runs
``helm template`` and ``kubeconform``, and returns structured results.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from sentinel.domain.charts import entities
from sentinel.utils import logs


def write_chart_to_dir(
    *,
    output: entities.ChartOutput,
    base_dir: Path | None = None,
) -> Path:
    """
    Write a ChartOutput's files to disk.

    :param output: the generated chart
    :param base_dir: parent directory; uses a temp dir if None
    :returns: path to the chart directory
    """
    if base_dir is None:
        base_dir = Path(tempfile.mkdtemp(prefix="sentinel-chart-"))

    chart_dir = base_dir / output.service_name
    for generated_file in output.files:
        file_path = chart_dir / generated_file.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(generated_file.content)

    return chart_dir


def run_helm_template(*, chart_dir: Path) -> entities.ValidationResult:
    """
    Run ``helm template`` on a chart directory.

    :param chart_dir: path to the chart directory
    :returns: ValidationResult with helm_template_ok and any errors/warnings
    """
    try:
        result = subprocess.run(
            ["helm", "template", "test-release", str(chart_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=["helm CLI not found — install helm to enable chart validation"],
            warnings=[],
        )
    except subprocess.TimeoutExpired:
        return entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=["helm template timed out after 30 seconds"],
            warnings=[],
        )

    errors: list[str] = []
    warnings: list[str] = []

    if result.returncode != 0:
        errors.append(result.stderr.strip() if result.stderr else "helm template failed with no error message")
    if result.stderr and result.returncode == 0:
        warnings.append(result.stderr.strip())

    return entities.ValidationResult(
        helm_template_ok=result.returncode == 0,
        kubeconform_ok=False,  # filled by run_kubeconform
        errors=errors,
        warnings=warnings,
    )


def run_kubeconform(*, rendered_yaml: str) -> entities.ValidationResult:
    """
    Run ``kubeconform`` on rendered YAML manifests.

    :param rendered_yaml: the rendered YAML string from ``helm template``
    :returns: ValidationResult with kubeconform_ok and any errors
    """
    try:
        result = subprocess.run(
            ["kubeconform", "-summary", "-strict"],
            input=rendered_yaml,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=["kubeconform CLI not found — install kubeconform to enable schema validation"],
            warnings=[],
        )
    except subprocess.TimeoutExpired:
        return entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=["kubeconform timed out after 30 seconds"],
            warnings=[],
        )

    errors: list[str] = []
    if result.returncode != 0:
        error_text = result.stdout.strip() or result.stderr.strip()
        if error_text:
            errors.extend(error_text.splitlines())

    return entities.ValidationResult(
        helm_template_ok=False,  # filled by caller
        kubeconform_ok=result.returncode == 0,
        errors=errors,
        warnings=[],
    )


def validate_chart(
    *,
    output: entities.ChartOutput,
    base_dir: Path | None = None,
) -> entities.ValidationResult:
    """
    Run full validation (helm template + kubeconform) on a chart output.

    :param output: the generated chart
    :param base_dir: parent directory for writing files; uses a temp dir if None
    :returns: combined ValidationResult
    """
    chart_dir = write_chart_to_dir(output=output, base_dir=base_dir)

    helm_result = run_helm_template(chart_dir=chart_dir)
    if not helm_result.helm_template_ok:
        logs.log_event(
            "chart_validation_failed",
            params={"service": output.service_name, "stage": "helm_template", "errors": helm_result.errors},
        )
        return helm_result

    # Get rendered YAML for kubeconform
    rendered = subprocess.run(
        ["helm", "template", "test-release", str(chart_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    kubeconform_result = run_kubeconform(rendered_yaml=rendered.stdout)

    return entities.ValidationResult(
        helm_template_ok=True,
        kubeconform_ok=kubeconform_result.kubeconform_ok,
        errors=helm_result.errors + kubeconform_result.errors,
        warnings=helm_result.warnings + kubeconform_result.warnings,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/charts/test_validation.py -v`
Expected: PASS (all 5 tests). Note: `TestRunHelmTemplate::test_returns_success_on_valid_chart` requires `helm` CLI installed. If not available, mark with `@pytest.mark.skipif`.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/domain/charts/validation.py tests/unit/domain/charts/test_validation.py
git commit -m "feat: add helm template and kubeconform validation runner"
```

---

### Task 7: PydanticAI Agent — Chart Request Parser

**Files:**
- Create: `src/sentinel/plugins/prompts/chart_request_parser.j2`
- Create: `src/sentinel/interfaces/graphs/agents/chart_request_parser.py`
- Test: `tests/unit/interfaces/graphs/agents/test_chart_request_parser.py`

- [ ] **Step 1: Create the Jinja2 prompt template**

Create `src/sentinel/plugins/prompts/chart_request_parser.j2`:

```jinja
{# Chart Request Parser — extracts a structured ChartSpec from a natural language request. #}
{% block system %}
You are an expert Kubernetes engineer. Given a natural language request to deploy a service,
extract a structured specification.

Extract these fields:
1. **service_name**: the name of the service/application
2. **image**: container image (if not specified, use "PLACEHOLDER" — the user will fill it in)
3. **ports**: list of ports (name, container_port, service_port, protocol). Default: http/8000/80/TCP
4. **resources**: CPU and memory requests/limits. Default: 100m/500m CPU, 128Mi/256Mi memory
5. **replicas**: min, max, and target CPU percentage. Default: 2/10/70
6. **dependencies**: external services the app connects to (name, host, port)
7. **environment_variables**: env vars needed (name, value or secret_ref)
8. **run_as_non_root**: whether to run as non-root (default: true)
9. **extra_resources**: any K8s resources mentioned that don't fit the above fields (e.g. CronJob, PVC)

Be precise. Infer reasonable defaults when the user doesn't specify values.
For dependencies, use Kubernetes DNS format: `<service>.<namespace>.svc.cluster.local`.
{% endblock %}

{% block user %}
Request: {{ raw_message }}
{% endblock %}
```

- [ ] **Step 2: Create the agent module**

Create `src/sentinel/interfaces/graphs/agents/chart_request_parser.py`:

```python
from __future__ import annotations

import dataclasses

from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.plugins import prompts


@dataclasses.dataclass
class Dependencies:
    raw_message: str


SYSTEM_PROMPT = prompts.load_system_prompt("chart_request_parser")

agent: Agent[Dependencies, entities.ChartSpec] = Agent(
    "test",
    deps_type=Dependencies,
    output_type=entities.ChartSpec,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_user_prompt(ctx: dataclasses.dataclass) -> str:  # type: ignore[type-arg]
    """Render the user block with the raw request message."""
    return prompts.render_user_prompt(
        "chart_request_parser",
        raw_message=ctx.deps.raw_message,
    )
```

- [ ] **Step 3: Write the test**

Create `tests/unit/interfaces/graphs/agents/test_chart_request_parser.py`:

```python
from __future__ import annotations

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import chart_request_parser


class TestChartRequestParserAgent:
    def test_agent_is_configured(self) -> None:
        # Given the chart request parser agent

        # When inspecting its configuration
        agent = chart_request_parser.agent

        # Then it has the correct output type and system prompt
        assert agent.output_type is entities.ChartSpec
        assert "Kubernetes engineer" in chart_request_parser.SYSTEM_PROMPT

    def test_dependencies_stores_raw_message(self) -> None:
        # Given a dependencies instance
        deps = chart_request_parser.Dependencies(raw_message="Deploy order-processor")

        # Then the raw message is accessible
        assert deps.raw_message == "Deploy order-processor"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/interfaces/graphs/agents/test_chart_request_parser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/plugins/prompts/chart_request_parser.j2 \
    src/sentinel/interfaces/graphs/agents/chart_request_parser.py \
    tests/unit/interfaces/graphs/agents/test_chart_request_parser.py
git commit -m "feat: add chart request parser PydanticAI agent"
```

---

### Task 8: PydanticAI Agent — Chart Generator

**Files:**
- Create: `src/sentinel/plugins/prompts/chart_generator.j2`
- Create: `src/sentinel/interfaces/graphs/agents/chart_generator.py`
- Test: `tests/unit/interfaces/graphs/agents/test_chart_generator.py`

- [ ] **Step 1: Create the Jinja2 prompt template**

Create `src/sentinel/plugins/prompts/chart_generator.j2`:

```jinja
{# Chart Generator — produces Helm chart files from a validated ChartSpec. #}
{% block system %}
You are an expert Kubernetes and Helm chart engineer. Given a structured service specification
and team policy, generate a complete Helm chart.

Generate these files as a list of (path, content) pairs:
1. **Chart.yaml** — chart metadata (apiVersion: v2)
2. **values.yaml** — default values
3. **values-dev.yaml** — development overrides (lower replicas, debug logging)
4. **values-prod.yaml** — production overrides (higher resources, replicas)
5. **templates/deployment.yaml** — Deployment manifest using values
6. **templates/service.yaml** — Service manifest
7. **templates/hpa.yaml** — HorizontalPodAutoscaler if replicas > 1
8. **templates/networkpolicy.yaml** — NetworkPolicy if required by team policy
9. **templates/_helpers.tpl** — template helpers (fullname, labels, selectorLabels)
10. **argocd-app.yaml** — ArgoCD Application manifest

Follow Helm best practices:
- Use `{{ "{{" }} .Values.<key> {{ "}}" }}` syntax in templates
- Include standard labels (app.kubernetes.io/name, instance, version, managed-by)
- Use resource requests and limits from values
- Set securityContext.runAsNonRoot if specified
- Use proper indentation (2 spaces for YAML)

For NetworkPolicy: if the team policy has allowed_egress entries, create egress rules for each.
If require_network_policy is true but no dependencies exist, create a deny-all egress policy.
{% endblock %}

{% block user %}
Service Specification:
{{ spec_json }}

Team Policy:
{{ policy_json }}
{% if previous_errors %}

Previous generation attempt failed validation with these errors:
{{ previous_errors }}

Fix the errors and regenerate the chart.
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Create the agent module**

Create `src/sentinel/interfaces/graphs/agents/chart_generator.py`:

```python
from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.plugins import prompts


class ChartGeneratorOutput(BaseModel):
    """Structured output from the chart generator agent."""

    files: list[entities.GeneratedFile]


@dataclasses.dataclass
class Dependencies:
    spec_json: str
    policy_json: str
    previous_errors: str = ""


SYSTEM_PROMPT = prompts.load_system_prompt("chart_generator")

agent: Agent[Dependencies, ChartGeneratorOutput] = Agent(
    "test",
    deps_type=Dependencies,
    output_type=ChartGeneratorOutput,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_user_prompt(ctx: dataclasses.dataclass) -> str:  # type: ignore[type-arg]
    """Render the user block with the spec, policy, and any previous errors."""
    return prompts.render_user_prompt(
        "chart_generator",
        spec_json=ctx.deps.spec_json,
        policy_json=ctx.deps.policy_json,
        previous_errors=ctx.deps.previous_errors,
    )
```

- [ ] **Step 3: Write the test**

Create `tests/unit/interfaces/graphs/agents/test_chart_generator.py`:

```python
from __future__ import annotations

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import chart_generator


class TestChartGeneratorAgent:
    def test_agent_is_configured(self) -> None:
        # Given the chart generator agent

        # When inspecting its configuration
        agent = chart_generator.agent

        # Then it has the correct output type and system prompt
        assert agent.output_type is chart_generator.ChartGeneratorOutput
        assert "Helm chart engineer" in chart_generator.SYSTEM_PROMPT

    def test_dependencies_stores_fields(self) -> None:
        # Given dependencies with spec and policy JSON
        deps = chart_generator.Dependencies(
            spec_json='{"service_name": "test"}',
            policy_json='{"team": "default"}',
            previous_errors="",
        )

        # Then fields are accessible
        assert "test" in deps.spec_json
        assert deps.previous_errors == ""


class TestChartGeneratorOutput:
    def test_stores_generated_files(self) -> None:
        # Given a generator output with files
        output = chart_generator.ChartGeneratorOutput(
            files=[
                entities.GeneratedFile(path="Chart.yaml", content="apiVersion: v2\n"),
            ]
        )

        # Then files are accessible
        assert len(output.files) == 1
        assert output.files[0].path == "Chart.yaml"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/interfaces/graphs/agents/test_chart_generator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/plugins/prompts/chart_generator.j2 \
    src/sentinel/interfaces/graphs/agents/chart_generator.py \
    tests/unit/interfaces/graphs/agents/test_chart_generator.py
git commit -m "feat: add chart generator PydanticAI agent"
```

---

### Task 9: Pipeline Reply Type

**Files:**
- Modify: `src/sentinel/domain/pipeline/types.py`
- Modify: `src/sentinel/interfaces/graphs/common.py`

- [ ] **Step 1: Add ChartGenerationReply to pipeline types**

Add to `src/sentinel/domain/pipeline/types.py`:

```python
class ChartGenerationReply(BaseModel):
    """Output from the chart generation pipeline."""

    service_name: str
    files: list[dict[str, str]] = []  # [{path: ..., content: ...}]
    confidence_score: float = 0.0
    policy_violations: list[dict[str, str]] = []
    validation_errors: list[str] = []
    validation_warnings: list[str] = []
    generation_attempts: int = 1
    pr_url: str | None = None
    approval_status: str | None = None  # "pending", "approved", "rejected", None
```

- [ ] **Step 2: Re-export from common.py**

Add to `src/sentinel/interfaces/graphs/common.py`:

```python
from sentinel.domain.pipeline.types import ChartGenerationReply as ChartGenerationReply
```

- [ ] **Step 3: Commit**

```bash
git add src/sentinel/domain/pipeline/types.py src/sentinel/interfaces/graphs/common.py
git commit -m "feat: add ChartGenerationReply pipeline output type"
```

---

### Task 10: Pydantic Graph Pipeline

**Files:**
- Create: `src/sentinel/interfaces/graphs/chart_generation.py`
- Test: `tests/unit/interfaces/graphs/test_chart_generation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/interfaces/graphs/test_chart_generation.py`:

```python
from __future__ import annotations

import dataclasses
from unittest import mock

import pytest

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs import chart_generation, common
from tests import factories


class TestParseRequestNode:
    @pytest.mark.asyncio
    async def test_transitions_to_load_policy(self) -> None:
        # Given a ParseRequest node and a mock agent that returns a ChartSpec
        mock_spec = factories.make_chart_spec()
        mock_result = mock.Mock()
        mock_result.output = mock_spec
        mock_result.all_messages.return_value = []

        state = chart_generation.State(
            request=factories.make_chart_request(),
        )
        deps = chart_generation.Dependencies(
            status_update_client=common.NoOpStatusUpdateClient(),
            parser_model="test-model",
            generator_model="test-model",
        )
        ctx = mock.Mock()
        ctx.state = state
        ctx.deps = deps

        node = chart_generation.ParseRequest()

        with mock.patch.object(
            chart_generation.chart_request_parser, "agent"
        ) as mock_agent:
            mock_agent.run = mock.AsyncMock(return_value=mock_result)

            # When running the node
            next_node = await node.run(ctx)

        # Then it transitions to LoadPolicy
        assert isinstance(next_node, chart_generation.LoadPolicy)


class TestMergeSpecNode:
    @pytest.mark.asyncio
    async def test_detects_policy_violations(self) -> None:
        # Given a spec that exceeds the policy memory cap
        spec = factories.make_chart_spec()
        spec = spec.model_copy(
            update={
                "resources": entities.ResourceSpec(
                    cpu_request="100m", cpu_limit="500m",
                    memory_request="128Mi", memory_limit="2Gi",
                ),
            }
        )
        policy = factories.make_team_policy(max_memory="512Mi")

        state = chart_generation.State(
            request=factories.make_chart_request(),
            spec=spec,
            policy=policy,
        )
        deps = chart_generation.Dependencies(
            status_update_client=common.NoOpStatusUpdateClient(),
            parser_model="test-model",
            generator_model="test-model",
        )
        ctx = mock.Mock()
        ctx.state = state
        ctx.deps = deps

        node = chart_generation.MergeSpec()

        # When running the node
        next_node = await node.run(ctx)

        # Then it ends with policy violations in the reply
        from pydantic_graph import End
        assert isinstance(next_node, End)
        assert len(next_node.data.policy_violations) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/interfaces/graphs/test_chart_generation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the pipeline graph**

Create `src/sentinel/interfaces/graphs/chart_generation.py`:

```python
from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from sentinel.domain.charts import confidence, entities, policies, validation
from sentinel.interfaces.graphs import common
from sentinel.interfaces.graphs.agents import chart_generator, chart_request_parser, utils
from sentinel.settings import get_settings
from sentinel.utils import logs


@dataclasses.dataclass
class Dependencies:
    status_update_client: common.StatusUpdateClient
    parser_model: str
    generator_model: str
    trace_collector: common.TraceCollector | None = None
    require_approval_below: float = 0.7
    max_retries: int = 3
    policies_dir: Path | None = None


@dataclasses.dataclass
class State:
    request: entities.ChartRequest
    spec: entities.ChartSpec | None = None
    policy: entities.TeamPolicy | None = None
    output: entities.ChartOutput | None = None
    validation_errors: list[str] = dataclasses.field(default_factory=list)
    generation_attempts: int = 0


@dataclasses.dataclass
class ParseRequest(BaseNode[State, Dependencies, common.ChartGenerationReply]):
    """Extract a structured ChartSpec from the natural language request."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> LoadPolicy | End[common.ChartGenerationReply]:
        await ctx.deps.status_update_client.update_status("Parsing chart request...")

        try:
            result = await chart_request_parser.agent.run(
                user_prompt=ctx.state.request.raw_message,
                model=utils.get_model_with_gateway(ctx.deps.parser_model),
                deps=chart_request_parser.Dependencies(
                    raw_message=ctx.state.request.raw_message,
                ),
            )
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"requester": ctx.state.request.requester, "node": "ParseRequest"},
            )
            return End(
                common.ChartGenerationReply(
                    service_name="unknown",
                    validation_errors=[f"Failed to parse request: {type(exc).__name__} — {exc}"],
                )
            )

        if ctx.deps.trace_collector:
            ctx.deps.trace_collector.record(
                agent_name="Chart Request Parser",
                messages=result.all_messages(),
            )

        ctx.state.spec = result.output
        logs.log_event(
            "chart_request_parsed",
            params={"service_name": result.output.service_name, "requester": ctx.state.request.requester},
        )
        return LoadPolicy()


@dataclasses.dataclass
class LoadPolicy(BaseNode[State, Dependencies, common.ChartGenerationReply]):
    """Load the team policy from the policy registry."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> MergeSpec | End[common.ChartGenerationReply]:
        await ctx.deps.status_update_client.update_status("Loading team policy...")

        try:
            kwargs = {}
            if ctx.deps.policies_dir:
                kwargs["policies_dir"] = ctx.deps.policies_dir
            policy = policies.load_team_policy(team=ctx.state.request.team, **kwargs)
        except FileNotFoundError as exc:
            logs.log_exception(
                exc,
                params={"team": ctx.state.request.team, "node": "LoadPolicy"},
            )
            return End(
                common.ChartGenerationReply(
                    service_name=ctx.state.spec.service_name if ctx.state.spec else "unknown",
                    validation_errors=[f"Team policy not found: {exc}"],
                )
            )

        ctx.state.policy = policy
        logs.log_event(
            "team_policy_loaded",
            params={"team": policy.team, "namespace": policy.namespace},
        )
        return MergeSpec()


@dataclasses.dataclass
class MergeSpec(BaseNode[State, Dependencies, common.ChartGenerationReply]):
    """Merge the chart spec with team policy, detecting violations."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> GenerateChart | End[common.ChartGenerationReply]:
        await ctx.deps.status_update_client.update_status("Checking policy compliance...")

        assert ctx.state.spec is not None
        assert ctx.state.policy is not None

        merged, violations = policies.merge_spec_with_policy(
            spec=ctx.state.spec,
            policy=ctx.state.policy,
        )
        ctx.state.spec = merged

        if violations:
            logs.log_event(
                "policy_violations_detected",
                params={
                    "service_name": merged.service_name,
                    "violation_count": len(violations),
                    "fields": [v.field for v in violations],
                },
            )
            return End(
                common.ChartGenerationReply(
                    service_name=merged.service_name,
                    policy_violations=[v.model_dump() for v in violations],
                    approval_status="policy_violation",
                )
            )

        return GenerateChart()


@dataclasses.dataclass
class GenerateChart(BaseNode[State, Dependencies, common.ChartGenerationReply]):
    """Generate Helm chart files using the PydanticAI agent."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> ValidateChart | End[common.ChartGenerationReply]:
        await ctx.deps.status_update_client.update_status("Generating Helm chart...")

        assert ctx.state.spec is not None
        assert ctx.state.policy is not None

        ctx.state.generation_attempts += 1

        try:
            result = await chart_generator.agent.run(
                user_prompt=f"Generate Helm chart for {ctx.state.spec.service_name}",
                model=utils.get_model_with_gateway(ctx.deps.generator_model),
                deps=chart_generator.Dependencies(
                    spec_json=ctx.state.spec.model_dump_json(indent=2),
                    policy_json=ctx.state.policy.model_dump_json(indent=2),
                    previous_errors="\n".join(ctx.state.validation_errors),
                ),
            )
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"service_name": ctx.state.spec.service_name, "node": "GenerateChart"},
            )
            return End(
                common.ChartGenerationReply(
                    service_name=ctx.state.spec.service_name,
                    validation_errors=[f"Chart generation failed: {type(exc).__name__} — {exc}"],
                    generation_attempts=ctx.state.generation_attempts,
                )
            )

        if ctx.deps.trace_collector:
            ctx.deps.trace_collector.record(
                agent_name="Chart Generator",
                messages=result.all_messages(),
            )

        ctx.state.output = entities.ChartOutput(
            service_name=ctx.state.spec.service_name,
            files=result.output.files,
            generation_attempts=ctx.state.generation_attempts,
        )

        logs.log_event(
            "chart_generated",
            params={
                "service_name": ctx.state.spec.service_name,
                "file_count": len(result.output.files),
                "attempt": ctx.state.generation_attempts,
            },
        )
        return ValidateChart()


@dataclasses.dataclass
class ValidateChart(BaseNode[State, Dependencies, common.ChartGenerationReply]):
    """Run helm template + kubeconform validation. Self-heal on syntax errors."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> ApprovalGate | GenerateChart | End[common.ChartGenerationReply]:
        await ctx.deps.status_update_client.update_status("Validating chart...")

        assert ctx.state.output is not None

        result = validation.validate_chart(output=ctx.state.output)
        ctx.state.output = ctx.state.output.model_copy(update={"validation_result": result})

        if not result.helm_template_ok or not result.kubeconform_ok:
            ctx.state.validation_errors = result.errors

            if ctx.state.generation_attempts < ctx.deps.max_retries:
                logs.log_event(
                    "chart_validation_failed_retrying",
                    params={
                        "service_name": ctx.state.output.service_name,
                        "attempt": ctx.state.generation_attempts,
                        "errors": result.errors,
                    },
                )
                return GenerateChart()

            logs.log_event(
                "chart_validation_failed_max_retries",
                params={
                    "service_name": ctx.state.output.service_name,
                    "attempts": ctx.state.generation_attempts,
                },
            )
            return End(
                common.ChartGenerationReply(
                    service_name=ctx.state.output.service_name,
                    validation_errors=result.errors,
                    validation_warnings=result.warnings,
                    generation_attempts=ctx.state.generation_attempts,
                )
            )

        return ApprovalGate()


@dataclasses.dataclass
class ApprovalGate(BaseNode[State, Dependencies, common.ChartGenerationReply]):
    """Gate chart output based on confidence score and auto-validate settings."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> CommitToGitOps | End[common.ChartGenerationReply]:
        assert ctx.state.output is not None
        assert ctx.state.spec is not None

        vr = ctx.state.output.validation_result
        score = confidence.calculate_chart_confidence(
            schema_valid=vr.kubeconform_ok if vr else False,
            template_renders=vr.helm_template_ok if vr else False,
            template_has_warnings=bool(vr.warnings) if vr else False,
            policy_violation_count=len(ctx.state.output.policy_violations),
            auto_resolved_count=0,
            requested_resource_count=len(ctx.state.spec.ports) + len(ctx.state.spec.dependencies) + 1,
            generated_resource_count=len(ctx.state.output.files),
            retry_count=ctx.state.generation_attempts - 1,
        )

        ctx.state.output = ctx.state.output.model_copy(update={"confidence_score": score})

        settings = get_settings()
        needs_approval = (
            not settings.k8s_chart_auto_validate
            or score < ctx.deps.require_approval_below
        )

        if needs_approval:
            logs.log_event(
                "chart_approval_required",
                params={"service_name": ctx.state.output.service_name, "confidence": score},
            )
            return End(
                common.ChartGenerationReply(
                    service_name=ctx.state.output.service_name,
                    files=[f.model_dump() for f in ctx.state.output.files],
                    confidence_score=score,
                    generation_attempts=ctx.state.generation_attempts,
                    approval_status="pending",
                )
            )

        return CommitToGitOps()


@dataclasses.dataclass
class CommitToGitOps(BaseNode[State, Dependencies, common.ChartGenerationReply]):
    """Write chart files to gitops/charts/ and create a PR."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> End[common.ChartGenerationReply]:
        await ctx.deps.status_update_client.update_status("Committing chart to GitOps directory...")

        assert ctx.state.output is not None

        from sentinel.settings import PROJECT_ROOT

        gitops_dir = PROJECT_ROOT / "gitops" / "charts"
        chart_dir = gitops_dir / ctx.state.output.service_name

        for generated_file in ctx.state.output.files:
            file_path = chart_dir / generated_file.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(generated_file.content)

        logs.log_event(
            "chart_committed_to_gitops",
            params={
                "service_name": ctx.state.output.service_name,
                "chart_dir": str(chart_dir),
                "file_count": len(ctx.state.output.files),
            },
        )

        return End(
            common.ChartGenerationReply(
                service_name=ctx.state.output.service_name,
                files=[f.model_dump() for f in ctx.state.output.files],
                confidence_score=ctx.state.output.confidence_score,
                generation_attempts=ctx.state.generation_attempts,
                approval_status="approved",
            )
        )


async def generate_chart(
    request: entities.ChartRequest,
    *,
    status_update_client: common.StatusUpdateClient | None = None,
    parser_model: str = "",
    generator_model: str = "",
    trace_collector: common.TraceCollector | None = None,
    require_approval_below: float = 0.7,
    max_retries: int = 3,
    policies_dir: Path | None = None,
) -> common.ChartGenerationReply:
    """
    Run the full chart generation pipeline.

    This is the main entry point for the chart generation graph.

    :param request: the parsed chart request
    :param status_update_client: optional status update client for UI feedback
    :param parser_model: LLM model for parsing (defaults to settings)
    :param generator_model: LLM model for generation (defaults to settings)
    :param trace_collector: optional trace collector for agent debugging
    :param require_approval_below: confidence threshold for human approval
    :param max_retries: maximum self-heal retries
    :param policies_dir: optional override for policy files directory
    """
    settings = get_settings()
    state = State(request=request)
    dependencies = Dependencies(
        status_update_client=status_update_client or common.NoOpStatusUpdateClient(),
        parser_model=parser_model or settings.k8s_chart_parser_llm,
        generator_model=generator_model or settings.k8s_chart_generator_llm,
        trace_collector=trace_collector,
        require_approval_below=require_approval_below,
        max_retries=max_retries or settings.k8s_chart_max_retries,
        policies_dir=policies_dir,
    )

    chart_graph = Graph(
        nodes=(
            ParseRequest,
            LoadPolicy,
            MergeSpec,
            GenerateChart,
            ValidateChart,
            ApprovalGate,
            CommitToGitOps,
        ),
    )

    result = await chart_graph.run(
        ParseRequest(),
        deps=dependencies,
        state=state,
    )
    return result.output
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/interfaces/graphs/test_chart_generation.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/interfaces/graphs/chart_generation.py \
    tests/unit/interfaces/graphs/test_chart_generation.py
git commit -m "feat: add Pydantic Graph pipeline for chart generation"
```

---

### Task 11: Lint and Type Check

- [ ] **Step 1: Run linting**

Run: `make lint-fix`

Fix any ruff or mypy issues that arise from the new code.

- [ ] **Step 2: Run full test suite**

Run: `make test`

Verify no regressions in existing tests.

- [ ] **Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve lint and type check issues in chart agent"
```

---

### Task 12: Streamlit UI Extension

**Files:**
- Modify: `src/sentinel/interfaces/chat/app.py`

- [ ] **Step 1: Read the current app.py to identify insertion points**

Read `src/sentinel/interfaces/chat/app.py` to find:
- Where sidebar sections are defined (for adding "K8s Chart Generator" section)
- Where the pipeline runner functions are defined (for adding `_run_chart_generation`)
- Where chat message handling dispatches to pipelines

- [ ] **Step 2: Add chart generation imports and runner function**

Add to imports section of `app.py`:

```python
from sentinel.domain.charts import entities as chart_entities
from sentinel.interfaces.graphs import chart_generation
```

Add the runner function (near the existing `_run_sre` function):

```python
async def _run_chart_generation(
    text: str,
    *,
    on_status: Callable[[str], None],
    trace_collector: common.TraceCollector | None = None,
) -> common.ChartGenerationReply:
    status_client = StreamlitStatusUpdateClient(on_status=on_status)
    request = chart_entities.ChartRequest(
        requester=st.session_state.get("user_id", "streamlit-user"),
        team=st.session_state.get("chart_team", "internal-tooling"),
        raw_message=text,
        requested_at=datetime.now(tz=UTC),
    )
    return await chart_generation.generate_chart(
        request,
        status_update_client=status_client,
        parser_model=_selected_model("chart_parser"),
        generator_model=_selected_model("chart_generator"),
        trace_collector=trace_collector,
    )
```

- [ ] **Step 3: Add sidebar section for chart generator**

Add to the sidebar (after existing scenario sections):

```python
st.sidebar.markdown("---")
st.sidebar.subheader("K8s Chart Generator")
chart_team = st.sidebar.selectbox(
    "Team Policy",
    options=["internal-tooling", "trading-infra"],
    key="chart_team",
)

chart_scenarios = {
    "Basic FastAPI service": "Deploy a FastAPI service called order-processor with 2-10 replicas, 256Mi memory, connecting to Postgres on port 5432",
    "Trading service (strict policy)": "Deploy a trading-engine service for the trading-infra team with Redis and Postgres dependencies, 512Mi memory, 4 replicas",
    "High-scale worker": "Deploy a data-pipeline-worker with Redis and Postgres, 2Gi memory, 5-50 replicas, running as non-root",
}

for label, scenario_text in chart_scenarios.items():
    if st.sidebar.button(label, key=f"chart_scenario_{label}"):
        st.session_state["pending_chart_scenario"] = scenario_text
```

- [ ] **Step 4: Add chat handling for chart generation results**

In the chat message handling section, add rendering for `ChartGenerationReply`:

```python
# After chart generation completes, render the results
if isinstance(reply, common.ChartGenerationReply):
    if reply.policy_violations:
        st.warning("Policy violations detected:")
        for v in reply.policy_violations:
            st.error(f"**{v['field']}**: requested {v['requested']}, allowed {v['allowed']}")

    if reply.validation_errors:
        st.error("Validation errors:")
        for err in reply.validation_errors:
            st.code(err)

    if reply.files:
        st.success(f"Generated {len(reply.files)} files (confidence: {reply.confidence_score:.0%})")
        for f in reply.files:
            with st.expander(f["path"]):
                st.code(f["content"], language="yaml")

    if reply.approval_status == "pending":
        st.info("Chart requires approval before committing to GitOps.")
```

- [ ] **Step 5: Run the app manually to verify**

Run: `uv run streamlit run src/sentinel/interfaces/chat/app.py`

Verify the sidebar shows "K8s Chart Generator" section with scenario buttons.

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/chat/app.py
git commit -m "feat: add K8s chart generator section to Streamlit chat UI"
```

---

### Task 13: End-to-End Verification

- [ ] **Step 1: Run full lint**

Run: `make lint`
Expected: PASS with no errors

- [ ] **Step 2: Run full test suite**

Run: `make test`
Expected: PASS with no regressions

- [ ] **Step 3: Run chart-specific tests**

Run: `uv run pytest tests/unit/domain/charts/ tests/unit/interfaces/graphs/test_chart_generation.py -v`
Expected: All chart agent tests pass

- [ ] **Step 4: Verify file structure**

Run: `find src/sentinel/domain/charts -type f && find src/sentinel/interfaces/graphs/agents -name "chart_*" -type f`

Expected output:
```
src/sentinel/domain/charts/__init__.py
src/sentinel/domain/charts/entities.py
src/sentinel/domain/charts/policies.py
src/sentinel/domain/charts/confidence.py
src/sentinel/domain/charts/validation.py
src/sentinel/interfaces/graphs/agents/chart_request_parser.py
src/sentinel/interfaces/graphs/agents/chart_generator.py
```

- [ ] **Step 5: Update plan status**

Mark `docs/plans/k8s-chart-coding-agent.md` status as `in-progress` and check off completed steps.
