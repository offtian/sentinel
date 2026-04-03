from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.domain.charts import entities, policies


class TestResolveTeam:
    def test_returns_team_for_known_user(self, tmp_path: Path):
        # Given a teams file mapping alice to platform
        teams_file = tmp_path / "_teams.yaml"
        teams_file.write_text("alice: platform\nbob: data-eng\n")

        # When resolving alice's team
        result = policies.resolve_team(user_id="alice", teams_file=teams_file)

        # Then the team is platform
        assert result == "platform"

    def test_raises_for_unknown_user(self, tmp_path: Path):
        # Given a teams file without charlie
        teams_file = tmp_path / "_teams.yaml"
        teams_file.write_text("alice: platform\n")

        # When resolving charlie's team
        # Then a ValueError is raised
        with pytest.raises(ValueError, match="Unknown user"):
            policies.resolve_team(user_id="charlie", teams_file=teams_file)


class TestLoadTeamPolicy:
    def test_loads_policy_from_yaml(self, tmp_path: Path):
        # Given a platform policy file
        policy_file = tmp_path / "platform.yaml"
        policy_file.write_text(
            "team: platform\n"
            "namespace: platform-prod\n"
            "max_memory: 2Gi\n"
            "max_cpu: 2000m\n"
            "max_replicas: 10\n"
            "require_network_policy: true\n"
            "require_non_root: true\n"
            "default_labels:\n"
            "  team: platform\n"
            "  env: production\n"
        )

        # When loading the policy
        result = policies.load_team_policy(team="platform", policies_dir=tmp_path)

        # Then all fields are populated
        assert result.team == "platform"
        assert result.namespace == "platform-prod"
        assert result.max_memory == "2Gi"
        assert result.max_replicas == 10
        assert result.require_non_root is True
        assert result.default_labels == {"team": "platform", "env": "production"}

    def test_loads_policy_with_egress_rules(self, tmp_path: Path):
        # Given a policy with allowed_egress
        policy_file = tmp_path / "platform.yaml"
        policy_file.write_text(
            "team: platform\n"
            "allowed_egress:\n"
            "  - host: redis.internal\n"
            "    port: 6379\n"
            "  - host: postgres.internal\n"
            "    port: 5432\n"
        )

        # When loading the policy
        result = policies.load_team_policy(team="platform", policies_dir=tmp_path)

        # Then egress rules are parsed
        assert len(result.allowed_egress) == 2
        assert result.allowed_egress[0].host == "redis.internal"
        assert result.allowed_egress[1].port == 5432

    def test_raises_for_missing_team(self, tmp_path: Path):
        # Given no policy file for 'unknown-team'
        # When loading the policy
        # Then a FileNotFoundError is raised
        with pytest.raises(FileNotFoundError, match="No policy file"):
            policies.load_team_policy(team="unknown-team", policies_dir=tmp_path)


class TestMergeSpecWithPolicy:
    def test_applies_policy_defaults_when_spec_has_no_resources(self):
        # Given a spec without resources and a policy with limits
        spec = entities.ChartSpec(service_name="api-gateway", image="myrepo/api:latest")
        policy = entities.TeamPolicy(
            team="platform",
            namespace="platform-prod",
            max_memory="2Gi",
            max_cpu="2000m",
            max_replicas=10,
            require_network_policy=True,
            require_non_root=True,
        )

        # When merging
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then run_as_non_root is enforced and no violations
        assert merged.run_as_non_root is True
        assert violations == ()

    def test_detects_memory_limit_violation(self):
        # Given a spec requesting more memory than policy allows
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:latest",
            resources=entities.ResourceSpec(memory_limit="4Gi", cpu_limit="500m"),
        )
        policy = entities.TeamPolicy(team="platform", max_memory="2Gi", max_cpu="2000m")

        # When merging
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then a memory violation is detected
        assert len(violations) == 1
        assert violations[0].field == "memory_limit"
        assert violations[0].requested == "4Gi"
        assert violations[0].allowed == "2Gi"

    def test_detects_replicas_violation(self):
        # Given a spec requesting more replicas than policy allows
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:latest",
            replicas=entities.ReplicaSpec(min_replicas=2, max_replicas=20),
        )
        policy = entities.TeamPolicy(team="platform", max_replicas=10)

        # When merging
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then a replicas violation is detected
        assert len(violations) == 1
        assert violations[0].field == "max_replicas"

    def test_enforces_non_root_when_policy_requires(self):
        # Given a spec with run_as_non_root=False but policy requires it
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:latest",
            run_as_non_root=False,
        )
        policy = entities.TeamPolicy(team="platform", require_non_root=True)

        # When merging
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then non_root is enforced on the merged spec
        assert merged.run_as_non_root is True

    def test_adds_network_policy_resource_when_required(self):
        # Given a spec without NetworkPolicy and a policy requiring it
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:latest",
            extra_resources=(),
        )
        policy = entities.TeamPolicy(team="platform", require_network_policy=True)

        # When merging
        merged, violations = policies.merge_spec_with_policy(spec=spec, policy=policy)

        # Then NetworkPolicy is added to extra_resources
        assert "NetworkPolicy" in merged.extra_resources
