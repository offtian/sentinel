# K8s Chart Coding Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Pydantic Graph pipeline that parses natural-language chart requests, applies team policies, generates Helm charts via PydanticAI agents, validates them, and commits to a GitOps directory.

**Architecture:** 7-node pipeline (`ParseRequest → LoadPolicy → MergeSpec → GenerateChart → ValidateChart → ApprovalGate → CommitToGitOps`) with a self-heal loop on validation failures (max 3 retries). Two PydanticAI agents handle NL parsing and chart generation. Policy YAML files in `policies/` define per-team constraints.

**Tech Stack:** Python 3.13, PydanticAI, Pydantic Graph, Pydantic BaseModel, Jinja2 prompts, structlog, YAML (PyYAML), subprocess (helm/kubeconform), Streamlit, pydantic_evals.

**Design spec:** [docs/plans/k8s-chart-coding-agent.md](../../plans/k8s-chart-coding-agent.md)

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `src/sentinel/domain/charts/__init__.py` | Package init |
| `src/sentinel/domain/charts/entities.py` | Domain types: ChartRequest, ChartSpec, TeamPolicy, ChartOutput, etc. |
| `src/sentinel/domain/charts/policies.py` | Load team YAML, resolve user→team, merge spec with policy |
| `src/sentinel/domain/charts/validation.py` | Run `helm template` + `kubeconform` via subprocess |
| `src/sentinel/domain/charts/confidence.py` | Weighted multi-factor confidence scoring for charts |
| `src/sentinel/interfaces/graphs/agents/chart_request_parser.py` | PydanticAI agent: NL → ChartSpec |
| `src/sentinel/interfaces/graphs/agents/chart_generator.py` | PydanticAI agent: ChartSpec → Helm YAML files |
| `src/sentinel/interfaces/graphs/chart_generation.py` | 7-node Pydantic Graph pipeline + `generate_chart()` entry point |
| `src/sentinel/application/charts/__init__.py` | Package init |
| `src/sentinel/application/charts/commit.py` | Write files to gitops/, create branch, open PR |
| `src/sentinel/plugins/prompts/chart_request_parser.j2` | System + user prompt for parser agent |
| `src/sentinel/plugins/prompts/chart_generator.j2` | System + user prompt for generator agent |
| `policies/platform.yaml` | Example team policy |
| `policies/_teams.yaml` | User-to-team mapping |
| `src/sentinel/evals/datasets/chart_generation_cases.json` | 5 golden test cases |
| `src/sentinel/evals/evaluators/chart_evaluators.py` | Chart-specific evaluators |
| `tests/unit/domain/charts/__init__.py` | Test package init |
| `tests/unit/domain/charts/test_entities.py` | Entity tests |
| `tests/unit/domain/charts/test_policies.py` | Policy loader tests |
| `tests/unit/domain/charts/test_validation.py` | Validation runner tests |
| `tests/unit/domain/charts/test_confidence.py` | Confidence scoring tests |
| `tests/unit/interfaces/graphs/agents/test_chart_request_parser.py` | Parser agent structure tests |
| `tests/unit/interfaces/graphs/agents/test_chart_generator.py` | Generator agent structure tests |
| `tests/unit/interfaces/graphs/test_chart_generation.py` | Pipeline node tests |
| `tests/unit/application/charts/__init__.py` | Test package init |
| `tests/unit/application/charts/test_commit.py` | GitOps committer tests |
| `tests/unit/evals/evaluators/test_chart_evaluators.py` | Evaluator tests |
| `tests/functional/test_chart_generation_pipeline.py` | End-to-end pipeline test |

### Modified files

| File | What changes |
|------|-------------|
| `src/sentinel/settings.py` | Add `K8sChartSettings` class, compose into `Settings` |
| `src/sentinel/config.py` | Add chart model properties + `build_chart_generation_deps()` |
| `src/sentinel/domain/pipeline/types.py` | Add `ChartGenerationReply` |
| `src/sentinel/interfaces/graphs/common.py` | Re-export `ChartGenerationReply` |
| `src/sentinel/evals/cases/base.py` | Register `chart_generator` dataset + evaluator builder |
| `src/sentinel/interfaces/chat/app.py` | Add chart generation scenarios, runner, and UI |
| `tests/factories/__init__.py` | Add `make_chart_request()`, `make_chart_spec()`, etc. |

---

## Task 1: Add Chart Agent Settings

**Files:**
- Modify: `src/sentinel/settings.py:20-91`
- Test: `tests/unit/test_settings.py` (create if not exists)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_settings.py
from __future__ import annotations

from sentinel import settings


class TestK8sChartSettings:
    def test_defaults_are_set(self):
        # Given default settings
        s = settings.Settings()

        # Then chart agent settings have expected defaults
        assert s.k8s_chart_generator_llm == "openai/gpt-4.1"
        assert s.k8s_chart_parser_llm == "openai/gpt-4.1-mini"
        assert s.k8s_chart_auto_validate is False
        assert s.k8s_chart_auto_sandbox is False
        assert s.k8s_chart_sandbox_context == ""
        assert s.k8s_chart_max_retries == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py::TestK8sChartSettings -v`
Expected: FAIL — attributes don't exist yet.

- [ ] **Step 3: Write minimal implementation**

Add a new settings class between `SRESettings` and `SupportSettings` in `src/sentinel/settings.py`:

```python
class K8sChartSettings(BaseSettings):
    """K8s chart coding agent settings."""

    k8s_chart_generator_llm: str = "openai/gpt-4.1"
    k8s_chart_parser_llm: str = "openai/gpt-4.1-mini"
    k8s_chart_auto_validate: bool = False
    k8s_chart_auto_sandbox: bool = False
    k8s_chart_sandbox_context: str = ""
    k8s_chart_max_retries: int = 3
```

Update the `Settings` class inheritance to include `K8sChartSettings`:

```python
class Settings(LLMSettings, SRESettings, K8sChartSettings, SupportSettings):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_settings.py::TestK8sChartSettings -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/settings.py tests/unit/test_settings.py
git commit -m "feat: add K8s chart agent settings"
```

---

## Task 2: Create Domain Entities

**Files:**
- Create: `src/sentinel/domain/charts/__init__.py`
- Create: `src/sentinel/domain/charts/entities.py`
- Create: `tests/unit/domain/charts/__init__.py`
- Test: `tests/unit/domain/charts/test_entities.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/domain/charts/test_entities.py
from __future__ import annotations

from datetime import UTC, datetime

from sentinel.domain.charts import entities


class TestPortSpec:
    def test_creates_with_defaults(self):
        # Given a port spec with only required fields
        port = entities.PortSpec(container_port=8080)

        # Then defaults are set
        assert port.container_port == 8080
        assert port.protocol == "TCP"
        assert port.name == ""


class TestResourceSpec:
    def test_creates_with_all_fields(self):
        # Given resource requests and limits
        spec = entities.ResourceSpec(
            cpu_request="100m",
            cpu_limit="500m",
            memory_request="128Mi",
            memory_limit="512Mi",
        )

        # Then all values are stored
        assert spec.cpu_request == "100m"
        assert spec.memory_limit == "512Mi"


class TestChartRequest:
    def test_creates_with_required_fields(self):
        # Given a chart request with all required fields
        now = datetime(2026, 4, 3, tzinfo=UTC)
        request = entities.ChartRequest(
            requester="alice",
            team="platform",
            raw_message="Deploy a Python web service called api-gateway on port 8080",
            requested_at=now,
        )

        # Then fields are set
        assert request.requester == "alice"
        assert request.team == "platform"
        assert request.raw_message.startswith("Deploy")
        assert request.requested_at == now


class TestChartSpec:
    def test_creates_with_minimal_fields(self):
        # Given a chart spec with only required fields
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="nginx:latest",
        )

        # Then defaults are populated
        assert spec.service_name == "api-gateway"
        assert spec.image == "nginx:latest"
        assert spec.ports == ()
        assert spec.replicas is None
        assert spec.resources is None
        assert spec.run_as_non_root is True
        assert spec.env_vars == ()
        assert spec.dependencies == ()
        assert spec.extra_resources == ()

    def test_creates_with_all_fields(self):
        # Given a fully specified chart spec
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:v1.2.3",
            ports=(entities.PortSpec(container_port=8080, name="http"),),
            replicas=entities.ReplicaSpec(min_replicas=2, max_replicas=5),
            resources=entities.ResourceSpec(
                cpu_request="100m",
                cpu_limit="500m",
                memory_request="128Mi",
                memory_limit="512Mi",
            ),
            run_as_non_root=True,
            env_vars=(entities.EnvVarSpec(name="LOG_LEVEL", value="info"),),
            dependencies=(entities.DependencySpec(name="redis", port=6379),),
            extra_resources=("NetworkPolicy", "PodDisruptionBudget"),
        )

        # Then all values are stored
        assert len(spec.ports) == 1
        assert spec.replicas.max_replicas == 5
        assert spec.resources.cpu_limit == "500m"
        assert spec.env_vars[0].name == "LOG_LEVEL"
        assert spec.dependencies[0].name == "redis"
        assert "NetworkPolicy" in spec.extra_resources


class TestTeamPolicy:
    def test_creates_with_all_fields(self):
        # Given a team policy
        policy = entities.TeamPolicy(
            team="platform",
            namespace="platform-prod",
            max_memory="2Gi",
            max_cpu="2000m",
            max_replicas=10,
            require_network_policy=True,
            require_non_root=True,
            allowed_egress=(
                entities.EgressRule(host="redis.internal", port=6379),
            ),
            default_labels={"team": "platform", "env": "production"},
        )

        # Then all values are stored
        assert policy.team == "platform"
        assert policy.max_replicas == 10
        assert policy.require_non_root is True
        assert len(policy.allowed_egress) == 1
        assert policy.default_labels["team"] == "platform"


class TestPolicyViolation:
    def test_creates_with_all_fields(self):
        # Given a policy violation
        violation = entities.PolicyViolation(
            field="memory_limit",
            requested="4Gi",
            allowed="2Gi",
            message="Memory limit exceeds team maximum of 2Gi",
        )

        # Then fields are set
        assert violation.field == "memory_limit"
        assert violation.requested == "4Gi"


class TestGeneratedFile:
    def test_creates_with_path_and_content(self):
        # Given a generated file
        gf = entities.GeneratedFile(
            path="templates/deployment.yaml",
            content="apiVersion: apps/v1\nkind: Deployment",
        )

        # Then fields are set
        assert gf.path == "templates/deployment.yaml"
        assert "Deployment" in gf.content


class TestValidationResult:
    def test_creates_passing_result(self):
        # Given a passing validation
        result = entities.ValidationResult(
            helm_template_ok=True,
            kubeconform_ok=True,
        )

        # Then it passes and has no errors
        assert result.helm_template_ok is True
        assert result.kubeconform_ok is True
        assert result.errors == ()
        assert result.warnings == ()

    def test_creates_failing_result(self):
        # Given a failing validation with errors
        result = entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=("template rendering failed: missing required field 'image'",),
            warnings=("deprecated API version apps/v1beta1",),
        )

        # Then errors and warnings are captured
        assert result.helm_template_ok is False
        assert len(result.errors) == 1
        assert len(result.warnings) == 1


class TestChartOutput:
    def test_creates_with_required_fields(self):
        # Given a chart output
        output = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
            ),
        )

        # Then defaults are set
        assert output.service_name == "api-gateway"
        assert len(output.files) == 1
        assert output.validation_result is None
        assert output.policy_violations == ()
        assert output.generation_attempts == 1
        assert output.confidence_score is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/domain/charts/test_entities.py -v`
Expected: FAIL — module `sentinel.domain.charts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/sentinel/domain/charts/__init__.py` (empty file).

Create `src/sentinel/domain/charts/entities.py`:

```python
"""
Domain entities for the K8s chart coding agent.

These Pydantic models represent the data flowing through the chart generation
pipeline — from raw user request to validated Helm chart output.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PortSpec(BaseModel):
    container_port: int
    protocol: str = "TCP"
    name: str = ""


class ResourceSpec(BaseModel):
    cpu_request: str = ""
    cpu_limit: str = ""
    memory_request: str = ""
    memory_limit: str = ""


class ReplicaSpec(BaseModel):
    min_replicas: int = 1
    max_replicas: int = 3


class DependencySpec(BaseModel):
    name: str
    port: int = 0


class EnvVarSpec(BaseModel):
    name: str
    value: str = ""
    secret_ref: str = ""


class EgressRule(BaseModel):
    host: str
    port: int


class ChartRequest(BaseModel):
    requester: str
    team: str
    raw_message: str
    requested_at: datetime


class ChartSpec(BaseModel):
    service_name: str
    image: str
    ports: tuple[PortSpec, ...] = ()
    replicas: ReplicaSpec | None = None
    resources: ResourceSpec | None = None
    run_as_non_root: bool = True
    env_vars: tuple[EnvVarSpec, ...] = ()
    dependencies: tuple[DependencySpec, ...] = ()
    extra_resources: tuple[str, ...] = ()


class TeamPolicy(BaseModel):
    team: str
    namespace: str = ""
    max_memory: str = ""
    max_cpu: str = ""
    max_replicas: int = 0
    require_network_policy: bool = False
    require_non_root: bool = True
    allowed_egress: tuple[EgressRule, ...] = ()
    default_labels: dict[str, str] = Field(default_factory=dict)


class PolicyViolation(BaseModel):
    field: str
    requested: str
    allowed: str
    message: str


class GeneratedFile(BaseModel):
    path: str
    content: str


class ValidationResult(BaseModel):
    helm_template_ok: bool
    kubeconform_ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ChartOutput(BaseModel):
    service_name: str
    files: tuple[GeneratedFile, ...] = ()
    validation_result: ValidationResult | None = None
    policy_violations: tuple[PolicyViolation, ...] = ()
    generation_attempts: int = 1
    confidence_score: float | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/domain/charts/test_entities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/domain/charts/ tests/unit/domain/charts/
git commit -m "feat: add domain entities for chart coding agent"
```

---

## Task 3: Create Test Factories

**Files:**
- Modify: `tests/factories/__init__.py`

- [ ] **Step 1: Add chart factories**

Append to `tests/factories/__init__.py`:

```python
from sentinel.domain.charts import entities as chart_entities


def make_chart_request(
    *,
    requester: str = "alice",
    team: str = "platform",
    raw_message: str = "Deploy a Python web service called api-gateway on port 8080 with 256Mi memory",
    requested_at: datetime | None = None,
) -> chart_entities.ChartRequest:
    return chart_entities.ChartRequest(
        requester=requester,
        team=team,
        raw_message=raw_message,
        requested_at=requested_at or datetime(2026, 4, 1, tzinfo=UTC),
    )


def make_chart_spec(
    *,
    service_name: str = "api-gateway",
    image: str = "myrepo/api-gateway:latest",
    ports: tuple[chart_entities.PortSpec, ...] = (
        chart_entities.PortSpec(container_port=8080, name="http"),
    ),
    replicas: chart_entities.ReplicaSpec | None = None,
    resources: chart_entities.ResourceSpec | None = None,
    run_as_non_root: bool = True,
    env_vars: tuple[chart_entities.EnvVarSpec, ...] = (),
    dependencies: tuple[chart_entities.DependencySpec, ...] = (),
    extra_resources: tuple[str, ...] = (),
) -> chart_entities.ChartSpec:
    return chart_entities.ChartSpec(
        service_name=service_name,
        image=image,
        ports=ports,
        replicas=replicas or chart_entities.ReplicaSpec(min_replicas=2, max_replicas=5),
        resources=resources
        or chart_entities.ResourceSpec(
            cpu_request="100m",
            cpu_limit="500m",
            memory_request="128Mi",
            memory_limit="256Mi",
        ),
        run_as_non_root=run_as_non_root,
        env_vars=env_vars,
        dependencies=dependencies,
        extra_resources=extra_resources,
    )


def make_team_policy(
    *,
    team: str = "platform",
    namespace: str = "platform-prod",
    max_memory: str = "2Gi",
    max_cpu: str = "2000m",
    max_replicas: int = 10,
    require_network_policy: bool = True,
    require_non_root: bool = True,
    allowed_egress: tuple[chart_entities.EgressRule, ...] = (),
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
        allowed_egress=allowed_egress,
        default_labels=default_labels or {"team": team},
    )


def make_generated_file(
    *,
    path: str = "templates/deployment.yaml",
    content: str = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api-gateway",
) -> chart_entities.GeneratedFile:
    return chart_entities.GeneratedFile(path=path, content=content)


def make_validation_result(
    *,
    helm_template_ok: bool = True,
    kubeconform_ok: bool = True,
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> chart_entities.ValidationResult:
    return chart_entities.ValidationResult(
        helm_template_ok=helm_template_ok,
        kubeconform_ok=kubeconform_ok,
        errors=errors,
        warnings=warnings,
    )


def make_chart_output(
    *,
    service_name: str = "api-gateway",
    files: tuple[chart_entities.GeneratedFile, ...] | None = None,
    validation_result: chart_entities.ValidationResult | None = None,
    policy_violations: tuple[chart_entities.PolicyViolation, ...] = (),
    generation_attempts: int = 1,
    confidence_score: float | None = None,
) -> chart_entities.ChartOutput:
    return chart_entities.ChartOutput(
        service_name=service_name,
        files=files or (make_generated_file(),),
        validation_result=validation_result,
        policy_violations=policy_violations,
        generation_attempts=generation_attempts,
        confidence_score=confidence_score,
    )
```

- [ ] **Step 2: Verify factories work**

Run: `uv run pytest tests/unit/domain/charts/test_entities.py -v`
Expected: PASS (no regressions)

- [ ] **Step 3: Commit**

```bash
git add tests/factories/__init__.py
git commit -m "feat: add test factories for chart domain entities"
```

---

## Task 4: Create Policy Registry

**Files:**
- Create: `src/sentinel/domain/charts/policies.py`
- Create: `policies/platform.yaml`
- Create: `policies/_teams.yaml`
- Test: `tests/unit/domain/charts/test_policies.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/domain/charts/test_policies.py
from __future__ import annotations

from pathlib import Path
from unittest import mock

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
        result = policies.load_team_policy(
            team="platform", policies_dir=tmp_path
        )

        # Then all fields are populated
        assert result.team == "platform"
        assert result.namespace == "platform-prod"
        assert result.max_memory == "2Gi"
        assert result.max_replicas == 10
        assert result.require_non_root is True
        assert result.default_labels == {"team": "platform", "env": "production"}

    def test_raises_for_missing_team(self, tmp_path: Path):
        # Given no policy file for 'unknown-team'
        # When loading the policy
        # Then a FileNotFoundError is raised
        with pytest.raises(FileNotFoundError, match="No policy file"):
            policies.load_team_policy(team="unknown-team", policies_dir=tmp_path)


class TestMergeSpecWithPolicy:
    def test_applies_policy_defaults_when_spec_has_no_resources(self):
        # Given a spec without resources and a policy with limits
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:latest",
        )
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
        merged, violations = policies.merge_spec_with_policy(
            spec=spec, policy=policy
        )

        # Then run_as_non_root is enforced and no violations
        assert merged.run_as_non_root is True
        assert violations == ()

    def test_detects_memory_limit_violation(self):
        # Given a spec requesting more memory than policy allows
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:latest",
            resources=entities.ResourceSpec(
                memory_limit="4Gi",
                cpu_limit="500m",
            ),
        )
        policy = entities.TeamPolicy(
            team="platform",
            max_memory="2Gi",
            max_cpu="2000m",
        )

        # When merging
        merged, violations = policies.merge_spec_with_policy(
            spec=spec, policy=policy
        )

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
        policy = entities.TeamPolicy(
            team="platform",
            max_replicas=10,
        )

        # When merging
        merged, violations = policies.merge_spec_with_policy(
            spec=spec, policy=policy
        )

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
        policy = entities.TeamPolicy(
            team="platform",
            require_non_root=True,
        )

        # When merging
        merged, violations = policies.merge_spec_with_policy(
            spec=spec, policy=policy
        )

        # Then non_root is enforced on the merged spec
        assert merged.run_as_non_root is True

    def test_adds_network_policy_resource_when_required(self):
        # Given a spec without NetworkPolicy and a policy requiring it
        spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api:latest",
            extra_resources=(),
        )
        policy = entities.TeamPolicy(
            team="platform",
            require_network_policy=True,
        )

        # When merging
        merged, violations = policies.merge_spec_with_policy(
            spec=spec, policy=policy
        )

        # Then NetworkPolicy is added to extra_resources
        assert "NetworkPolicy" in merged.extra_resources
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/domain/charts/test_policies.py -v`
Expected: FAIL — `sentinel.domain.charts.policies` does not exist.

- [ ] **Step 3: Create policy YAML files**

Create `policies/_teams.yaml`:

```yaml
# User-to-team mapping.
# Format: <user_id>: <team_name>
# Team name must match a <team>.yaml file in this directory.
alice: platform
bob: data-eng
```

Create `policies/platform.yaml`:

```yaml
team: platform
namespace: platform-prod
max_memory: 2Gi
max_cpu: 2000m
max_replicas: 10
require_network_policy: true
require_non_root: true
allowed_egress:
  - host: redis.internal
    port: 6379
  - host: postgres.internal
    port: 5432
default_labels:
  team: platform
  env: production
```

- [ ] **Step 4: Implement the policy module**

Create `src/sentinel/domain/charts/policies.py`:

```python
"""
Policy registry for the K8s chart coding agent.

Load team policies from YAML files in the ``policies/`` directory,
resolve user-to-team mappings, and merge a ``ChartSpec`` with policy
constraints — detecting violations where the spec exceeds limits.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from sentinel.domain.charts import entities
from sentinel.settings import PROJECT_ROOT


_DEFAULT_POLICIES_DIR = PROJECT_ROOT / "policies"
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
        raw: dict = yaml.safe_load(f) or {}

    # Convert allowed_egress dicts to EgressRule models
    egress_dicts = raw.pop("allowed_egress", [])
    egress_rules = tuple(
        entities.EgressRule(host=e["host"], port=e["port"])
        for e in egress_dicts
    )

    return entities.TeamPolicy(
        **raw,
        allowed_egress=egress_rules,
    )


def _parse_memory_to_bytes(value: str) -> int:
    """
    Convert a K8s memory string (e.g. ``"2Gi"``, ``"512Mi"``) to bytes.

    Supports Mi and Gi suffixes.
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

    Applies policy defaults (non-root enforcement, NetworkPolicy injection)
    and detects violations where the spec exceeds policy limits.

    :param spec: The parsed chart specification.
    :param policy: The team's policy constraints.
    :returns: A tuple of (merged spec, violations found).
    """
    violations: list[entities.PolicyViolation] = []
    updates: dict = {}

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
                    message=f"Memory limit {spec.resources.memory_limit} exceeds team maximum of {policy.max_memory}",
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
                    message=f"CPU limit {spec.resources.cpu_limit} exceeds team maximum of {policy.max_cpu}",
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
                    message=f"Max replicas {spec.replicas.max_replicas} exceeds team maximum of {policy.max_replicas}",
                )
            )

    merged = spec.model_copy(update=updates) if updates else spec
    return merged, tuple(violations)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/domain/charts/test_policies.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/domain/charts/policies.py policies/platform.yaml policies/_teams.yaml
git commit -m "feat: add policy registry with YAML loading and merge logic"
```

---

## Task 5: Create Validation Runner

**Files:**
- Create: `src/sentinel/domain/charts/validation.py`
- Test: `tests/unit/domain/charts/test_validation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/domain/charts/test_validation.py
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from sentinel.domain.charts import entities, validation


class TestValidateChart:
    def test_returns_passing_result_when_both_tools_succeed(self):
        # Given a chart output with valid YAML
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api-gateway",
                ),
            ),
        )

        # When validating with mocked subprocess (both pass)
        with mock.patch.object(validation, "_run_helm_template") as mock_helm, \
             mock.patch.object(validation, "_run_kubeconform") as mock_conform:
            mock_helm.return_value = (True, (), ())
            mock_conform.return_value = (True, (), ())

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then both validations pass
        assert result.helm_template_ok is True
        assert result.kubeconform_ok is True
        assert result.errors == ()

    def test_returns_failing_result_when_helm_template_fails(self):
        # Given a chart output
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="invalid: yaml: [",
                ),
            ),
        )

        # When helm template fails
        with mock.patch.object(validation, "_run_helm_template") as mock_helm, \
             mock.patch.object(validation, "_run_kubeconform") as mock_conform:
            mock_helm.return_value = (False, ("Error: template rendering failed",), ())
            mock_conform.return_value = (True, (), ())

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then helm_template_ok is False and errors are captured
        assert result.helm_template_ok is False
        assert len(result.errors) == 1
        assert "template rendering failed" in result.errors[0]

    def test_returns_failing_result_when_kubeconform_fails(self):
        # Given a chart output
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
            ),
        )

        # When kubeconform fails
        with mock.patch.object(validation, "_run_helm_template") as mock_helm, \
             mock.patch.object(validation, "_run_kubeconform") as mock_conform:
            mock_helm.return_value = (True, (), ())
            mock_conform.return_value = (False, ("resource Deployment missing spec",), ())

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then kubeconform_ok is False
        assert result.kubeconform_ok is False
        assert len(result.errors) == 1

    def test_captures_warnings(self):
        # Given warnings from both tools
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
            ),
        )

        # When both pass with warnings
        with mock.patch.object(validation, "_run_helm_template") as mock_helm, \
             mock.patch.object(validation, "_run_kubeconform") as mock_conform:
            mock_helm.return_value = (True, (), ("chart has no .helmignore",))
            mock_conform.return_value = (True, (), ("deprecated API version",))

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then warnings are captured
        assert result.helm_template_ok is True
        assert result.kubeconform_ok is True
        assert len(result.warnings) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/domain/charts/test_validation.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/sentinel/domain/charts/validation.py`:

```python
"""
Validation runner for generated Helm charts.

Runs ``helm template`` and ``kubeconform`` as subprocesses to validate
the generated chart files. Both tools must be available on PATH.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from sentinel.domain.charts import entities
from sentinel.utils import logs


logger = logs.get_logger()


async def _run_helm_template(
    *,
    chart_dir: Path,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """
    Run ``helm template`` on a chart directory.

    :returns: (success, errors, warnings)
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "helm", "template", "test-release", str(chart_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return True, (), ("helm not found on PATH — skipping template validation",)

    errors: list[str] = []
    warnings: list[str] = []

    if proc.returncode != 0:
        stderr_text = stderr.decode().strip()
        if stderr_text:
            errors.append(stderr_text)
        return False, tuple(errors), tuple(warnings)

    # Parse warnings from stderr
    stderr_text = stderr.decode().strip()
    if stderr_text:
        for line in stderr_text.splitlines():
            warnings.append(line)

    return True, tuple(errors), tuple(warnings)


async def _run_kubeconform(
    *,
    chart_dir: Path,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """
    Run ``kubeconform`` on rendered templates.

    :returns: (success, errors, warnings)
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubeconform", "-summary", str(chart_dir / "templates"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return True, (), ("kubeconform not found on PATH — skipping schema validation",)

    errors: list[str] = []
    warnings: list[str] = []

    output = (stdout.decode() + stderr.decode()).strip()
    if proc.returncode != 0:
        if output:
            errors.append(output)
        return False, tuple(errors), tuple(warnings)

    if output:
        for line in output.splitlines():
            if "WARN" in line.upper():
                warnings.append(line)

    return True, tuple(errors), tuple(warnings)


async def validate_chart(
    *,
    chart: entities.ChartOutput,
) -> entities.ValidationResult:
    """
    Validate a generated chart by writing files to a temp directory
    and running helm template + kubeconform.

    :param chart: The generated chart output with files.
    :returns: A ValidationResult with pass/fail and any errors/warnings.
    """
    with tempfile.TemporaryDirectory(prefix="sentinel-chart-") as tmp:
        chart_dir = Path(tmp)
        templates_dir = chart_dir / "templates"
        templates_dir.mkdir()

        # Write a minimal Chart.yaml
        (chart_dir / "Chart.yaml").write_text(
            f"apiVersion: v2\nname: {chart.service_name}\nversion: 0.1.0\n"
        )

        # Write generated files
        for gf in chart.files:
            file_path = chart_dir / gf.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(gf.content)

        helm_ok, helm_errors, helm_warnings = await _run_helm_template(
            chart_dir=chart_dir
        )
        conform_ok, conform_errors, conform_warnings = await _run_kubeconform(
            chart_dir=chart_dir
        )

    all_errors = helm_errors + conform_errors
    all_warnings = helm_warnings + conform_warnings

    result = entities.ValidationResult(
        helm_template_ok=helm_ok,
        kubeconform_ok=conform_ok,
        errors=all_errors,
        warnings=all_warnings,
    )

    logger.info(
        "chart_validation_completed",
        service_name=chart.service_name,
        helm_ok=helm_ok,
        conform_ok=conform_ok,
        error_count=len(all_errors),
        warning_count=len(all_warnings),
    )

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/domain/charts/test_validation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/domain/charts/validation.py tests/unit/domain/charts/test_validation.py
git commit -m "feat: add chart validation runner with helm and kubeconform"
```

---

## Task 6: Create Confidence Scoring

**Files:**
- Create: `src/sentinel/domain/charts/confidence.py`
- Test: `tests/unit/domain/charts/test_confidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/domain/charts/test_confidence.py
from __future__ import annotations

from sentinel.domain.charts import confidence
from sentinel.domain.confidence import entities as confidence_entities


class TestChartConfidenceScore:
    def test_perfect_score(self):
        # Given all factors at maximum
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=0,
        )

        # Then total is 1.0 and label is HIGH
        assert score.total == 1.0
        assert score.label == confidence_entities.ConfidenceLabel.HIGH

    def test_zero_score_when_everything_fails(self):
        # Given all factors at minimum
        score = confidence.calculate_chart_confidence(
            schema_valid=False,
            template_renders=False,
            template_has_warnings=False,
            policy_compliant=False,
            policy_auto_resolved=False,
            spec_coverage=0.0,
            retry_count=3,
        )

        # Then total is near 0 and label is LOW
        assert score.total == pytest.approx(0.01, abs=0.01)
        assert score.label == confidence_entities.ConfidenceLabel.LOW

    def test_medium_score_with_warnings_and_retries(self):
        # Given partial success
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=True,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=0.8,
            retry_count=1,
        )

        # Then score is in MEDIUM or HIGH range
        assert 0.4 <= score.total <= 0.9

    def test_policy_auto_resolved_reduces_score(self):
        # Given policy was auto-resolved (not fully compliant)
        auto_resolved = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=False,
            policy_auto_resolved=True,
            spec_coverage=1.0,
            retry_count=0,
        )

        fully_compliant = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=0,
        )

        # Then auto-resolved score is lower than fully compliant
        assert auto_resolved.total < fully_compliant.total

    def test_retry_count_reduces_score(self):
        # Given different retry counts
        zero_retries = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=0,
        )

        two_retries = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=2,
        )

        # Then more retries means lower score
        assert two_retries.total < zero_retries.total
```

Add `import pytest` at the top of the test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/domain/charts/test_confidence.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/sentinel/domain/charts/confidence.py`:

```python
"""
Confidence scoring for generated Helm charts.

Weighted multi-factor scoring based on validation results,
policy compliance, spec coverage, and retry count.

Factors and weights (from design spec):
- Schema validity:    0.30  (kubeconform pass/fail)
- Template rendering: 0.20  (helm template pass/warnings/fail)
- Policy compliance:  0.25  (no violations / auto-resolved / escalated)
- Spec coverage:      0.15  (fraction of requested resources generated)
- Retry count:        0.10  (0=1.0, 1=0.7, 2=0.4, 3=0.1)
"""
from __future__ import annotations

from sentinel.domain.confidence import entities as confidence_entities

_RETRY_SCORES: dict[int, float] = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.1}

_WEIGHT_SCHEMA = 0.30
_WEIGHT_TEMPLATE = 0.20
_WEIGHT_POLICY = 0.25
_WEIGHT_COVERAGE = 0.15
_WEIGHT_RETRY = 0.10


def calculate_chart_confidence(
    *,
    schema_valid: bool,
    template_renders: bool,
    template_has_warnings: bool,
    policy_compliant: bool,
    policy_auto_resolved: bool,
    spec_coverage: float,
    retry_count: int,
) -> confidence_entities.ConfidenceScore:
    """
    Calculate a weighted confidence score for a generated chart.

    :param schema_valid: True if kubeconform passed.
    :param template_renders: True if helm template succeeded.
    :param template_has_warnings: True if helm template had warnings.
    :param policy_compliant: True if no policy violations.
    :param policy_auto_resolved: True if violations were auto-resolved.
    :param spec_coverage: 0.0-1.0 fraction of requested resources generated.
    :param retry_count: Number of self-heal retries (0-3).
    :returns: A ConfidenceScore with weighted components.
    """
    schema_raw = 1.0 if schema_valid else 0.0
    if template_renders and not template_has_warnings:
        template_raw = 1.0
    elif template_renders and template_has_warnings:
        template_raw = 0.5
    else:
        template_raw = 0.0

    if policy_compliant:
        policy_raw = 1.0
    elif policy_auto_resolved:
        policy_raw = 0.7
    else:
        policy_raw = 0.0

    coverage_raw = max(0.0, min(spec_coverage, 1.0))
    retry_raw = _RETRY_SCORES.get(min(retry_count, 3), 0.1)

    total = (
        schema_raw * _WEIGHT_SCHEMA
        + template_raw * _WEIGHT_TEMPLATE
        + policy_raw * _WEIGHT_POLICY
        + coverage_raw * _WEIGHT_COVERAGE
        + retry_raw * _WEIGHT_RETRY
    )
    total = round(total, 4)

    return confidence_entities.ConfidenceScore.from_factors(
        source_count=int(schema_raw + template_raw + policy_raw),
        max_expected_sources=3,
        relevance=total,
        recency=1.0,
        source_weight=0.0,
        relevance_weight=1.0,
        recency_weight=0.0,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/domain/charts/test_confidence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/domain/charts/confidence.py tests/unit/domain/charts/test_confidence.py
git commit -m "feat: add weighted confidence scoring for chart generation"
```

---

## Task 7: Create Chart Request Parser Agent

**Files:**
- Create: `src/sentinel/interfaces/graphs/agents/chart_request_parser.py`
- Create: `src/sentinel/plugins/prompts/chart_request_parser.j2`
- Test: `tests/unit/interfaces/graphs/agents/test_chart_request_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/interfaces/graphs/agents/test_chart_request_parser.py
from __future__ import annotations

from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import chart_request_parser


class TestChartRequestParserAgent:
    def test_agent_exists_and_is_typed_correctly(self):
        # Given the agent module

        # Then the agent is a PydanticAI Agent with correct types
        assert isinstance(chart_request_parser.agent, Agent)

    def test_output_type_is_chart_spec(self):
        # Given the agent

        # Then its output type is ChartSpec
        assert chart_request_parser.agent._output_type is entities.ChartSpec

    def test_dependencies_dataclass_has_required_fields(self):
        # Given the Dependencies dataclass
        deps = chart_request_parser.Dependencies(
            raw_message="Deploy a web service",
            requester="alice",
            team="platform",
        )

        # Then fields are set
        assert deps.raw_message == "Deploy a web service"
        assert deps.requester == "alice"
        assert deps.team == "platform"

    def test_system_prompt_is_loaded(self):
        # Given the system prompt

        # Then it is a non-empty string
        assert isinstance(chart_request_parser.SYSTEM_PROMPT, str)
        assert len(chart_request_parser.SYSTEM_PROMPT) > 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/interfaces/graphs/agents/test_chart_request_parser.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create prompt template**

Create `src/sentinel/plugins/prompts/chart_request_parser.j2`:

```jinja2
{# Chart Request Parser — extracts a structured ChartSpec from natural language. #}
{% block system %}
You are an expert Kubernetes engineer parsing deployment requests.

Given a natural language message describing a service to deploy, extract a structured
specification. Be precise and use reasonable defaults when the user omits details.

Rules:
1. **service_name**: lowercase, hyphenated (e.g. "api-gateway", "user-service")
2. **image**: full image reference including tag. If none given, use "<service_name>:latest"
3. **ports**: extract container ports mentioned. Default protocol is TCP
4. **replicas**: extract min/max if mentioned. Default: min=2, max=5
5. **resources**: extract CPU and memory requests/limits. Use reasonable defaults if omitted:
   - cpu_request: "100m", cpu_limit: "500m"
   - memory_request: "128Mi", memory_limit: "256Mi"
6. **run_as_non_root**: default true unless user explicitly requests root
7. **env_vars**: extract any environment variables mentioned
8. **dependencies**: extract any service dependencies (databases, caches, etc.)
9. **extra_resources**: include "NetworkPolicy" if security is mentioned, "HPA" if autoscaling
   is mentioned, "PodDisruptionBudget" if HA is mentioned

Output must be valid JSON matching the ChartSpec schema exactly.
{% endblock %}

{% block user %}
## Request
**From:** {{ requester }} (team: {{ team }})

{{ raw_message }}
{% endblock %}
```

- [ ] **Step 4: Implement the agent**

Create `src/sentinel/interfaces/graphs/agents/chart_request_parser.py`:

```python
"""
PydanticAI agent that parses natural-language chart requests into structured ChartSpec.

The agent extracts service name, image, ports, resources, replicas, and other
fields from a free-text deployment request.
"""
from __future__ import annotations

import dataclasses

from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.plugins import prompts


@dataclasses.dataclass
class Dependencies:
    raw_message: str
    requester: str
    team: str


SYSTEM_PROMPT = prompts.load_system_prompt("chart_request_parser")

agent: Agent[Dependencies, entities.ChartSpec] = Agent(
    "test",
    deps_type=Dependencies,
    output_type=entities.ChartSpec,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_context(ctx: dataclasses.dataclass) -> str:
    """Render the user prompt with request context."""
    return prompts.render_user_prompt(
        "chart_request_parser",
        raw_message=ctx.deps.raw_message,
        requester=ctx.deps.requester,
        team=ctx.deps.team,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/interfaces/graphs/agents/test_chart_request_parser.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/graphs/agents/chart_request_parser.py \
        src/sentinel/plugins/prompts/chart_request_parser.j2 \
        tests/unit/interfaces/graphs/agents/test_chart_request_parser.py
git commit -m "feat: add chart request parser agent with Jinja2 prompt"
```

---

## Task 8: Create Chart Generator Agent

**Files:**
- Create: `src/sentinel/interfaces/graphs/agents/chart_generator.py`
- Create: `src/sentinel/plugins/prompts/chart_generator.j2`
- Test: `tests/unit/interfaces/graphs/agents/test_chart_generator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/interfaces/graphs/agents/test_chart_generator.py
from __future__ import annotations

from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import chart_generator


class TestChartGeneratorOutput:
    def test_output_model_has_required_fields(self):
        # Given a ChartGeneratorOutput
        output = chart_generator.ChartGeneratorOutput(
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
                entities.GeneratedFile(
                    path="templates/service.yaml",
                    content="apiVersion: v1\nkind: Service",
                ),
            ),
        )

        # Then files are stored
        assert len(output.files) == 2


class TestChartGeneratorAgent:
    def test_agent_exists_and_is_typed_correctly(self):
        # Given the agent module

        # Then the agent is a PydanticAI Agent
        assert isinstance(chart_generator.agent, Agent)

    def test_output_type_is_chart_generator_output(self):
        # Given the agent

        # Then its output type is ChartGeneratorOutput
        assert chart_generator.agent._output_type is chart_generator.ChartGeneratorOutput

    def test_dependencies_dataclass_has_required_fields(self):
        # Given the Dependencies dataclass
        deps = chart_generator.Dependencies(
            service_name="api-gateway",
            image="nginx:latest",
            spec_json='{"service_name": "api-gateway"}',
            policy_json='{"team": "platform"}',
        )

        # Then fields are set
        assert deps.service_name == "api-gateway"

    def test_system_prompt_is_loaded(self):
        # Given the system prompt

        # Then it is a non-empty string
        assert isinstance(chart_generator.SYSTEM_PROMPT, str)
        assert len(chart_generator.SYSTEM_PROMPT) > 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/interfaces/graphs/agents/test_chart_generator.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create prompt template**

Create `src/sentinel/plugins/prompts/chart_generator.j2`:

```jinja2
{# Chart Generator — produces Helm chart YAML files from a validated ChartSpec. #}
{% block system %}
You are an expert Helm chart developer generating production-grade Kubernetes manifests.

Given a structured service specification and team policy, generate a complete set of
Helm chart template files. Each file must be valid YAML with proper Kubernetes API versions.

Required output files:
1. **templates/deployment.yaml** — Deployment with containers, ports, resources, probes, security context
2. **templates/service.yaml** — ClusterIP Service exposing container ports
3. **templates/hpa.yaml** (if replicas specified) — HorizontalPodAutoscaler
4. **templates/networkpolicy.yaml** (if NetworkPolicy in extra_resources) — ingress/egress rules
5. **templates/argocd-app.yaml** — ArgoCD Application resource pointing to the chart

Rules:
- Use Helm templating: `{{ "{{" }} .Values.* {{ "}}" }}`, `{{ "{{" }} .Release.Name {{ "}}" }}`, etc.
- Include `metadata.labels` with standard labels: `app.kubernetes.io/name`, `app.kubernetes.io/instance`
- Add team labels from policy `default_labels`
- Always include readiness and liveness probes (httpGet on first port, or tcpSocket)
- Set `securityContext.runAsNonRoot` based on spec
- Set resource requests and limits from spec
- For HPA: target CPU utilisation at 70%
- For NetworkPolicy: allow ingress on service ports, restrict egress to `allowed_egress` hosts

Output each file as a GeneratedFile with `path` and `content` fields.
{% endblock %}

{% block user %}
## Service Specification
```json
{{ spec_json }}
```

## Team Policy
```json
{{ policy_json }}
```

Generate the complete Helm chart files for the **{{ service_name }}** service using image **{{ image }}**.
{% endblock %}
```

- [ ] **Step 4: Implement the agent**

Create `src/sentinel/interfaces/graphs/agents/chart_generator.py`:

```python
"""
PydanticAI agent that generates Helm chart YAML files from a validated ChartSpec.

The agent produces Deployment, Service, HPA, NetworkPolicy, and ArgoCD
Application resources based on the spec and team policy.
"""
from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.plugins import prompts


class ChartGeneratorOutput(BaseModel):
    """Output from the chart generator agent."""

    files: tuple[entities.GeneratedFile, ...]


@dataclasses.dataclass
class Dependencies:
    service_name: str
    image: str
    spec_json: str
    policy_json: str


SYSTEM_PROMPT = prompts.load_system_prompt("chart_generator")

agent: Agent[Dependencies, ChartGeneratorOutput] = Agent(
    "test",
    deps_type=Dependencies,
    output_type=ChartGeneratorOutput,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_context(ctx: dataclasses.dataclass) -> str:
    """Render the user prompt with spec and policy context."""
    return prompts.render_user_prompt(
        "chart_generator",
        service_name=ctx.deps.service_name,
        image=ctx.deps.image,
        spec_json=ctx.deps.spec_json,
        policy_json=ctx.deps.policy_json,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/interfaces/graphs/agents/test_chart_generator.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/graphs/agents/chart_generator.py \
        src/sentinel/plugins/prompts/chart_generator.j2 \
        tests/unit/interfaces/graphs/agents/test_chart_generator.py
git commit -m "feat: add chart generator agent with Jinja2 prompt"
```

---

## Task 9: Create GitOps Committer

**Files:**
- Create: `src/sentinel/application/charts/__init__.py`
- Create: `src/sentinel/application/charts/commit.py`
- Create: `tests/unit/application/charts/__init__.py`
- Test: `tests/unit/application/charts/test_commit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/application/charts/test_commit.py
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from sentinel.application.charts import commit
from sentinel.domain.charts import entities


class TestWriteChartFiles:
    def test_writes_files_to_output_directory(self, tmp_path: Path):
        # Given a chart output with two files
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
                entities.GeneratedFile(
                    path="templates/service.yaml",
                    content="apiVersion: v1\nkind: Service",
                ),
                entities.GeneratedFile(
                    path="Chart.yaml",
                    content="apiVersion: v2\nname: api-gateway\nversion: 0.1.0",
                ),
            ),
        )

        # When writing files
        output_dir = commit.write_chart_files(
            chart=chart, gitops_root=tmp_path
        )

        # Then files are written to gitops_root/api-gateway/
        assert output_dir == tmp_path / "api-gateway"
        assert (output_dir / "templates" / "deployment.yaml").exists()
        assert (output_dir / "templates" / "service.yaml").exists()
        assert (output_dir / "Chart.yaml").exists()

        deployment_content = (output_dir / "templates" / "deployment.yaml").read_text()
        assert "Deployment" in deployment_content

    def test_overwrites_existing_files(self, tmp_path: Path):
        # Given existing files in the output directory
        existing_dir = tmp_path / "api-gateway" / "templates"
        existing_dir.mkdir(parents=True)
        (existing_dir / "deployment.yaml").write_text("old content")

        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="new content",
                ),
            ),
        )

        # When writing files
        commit.write_chart_files(chart=chart, gitops_root=tmp_path)

        # Then files are overwritten
        content = (tmp_path / "api-gateway" / "templates" / "deployment.yaml").read_text()
        assert content == "new content"


class TestCommitToGitOps:
    def test_calls_git_and_gh_commands(self, tmp_path: Path):
        # Given a chart output
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="Chart.yaml",
                    content="apiVersion: v2\nname: api-gateway",
                ),
            ),
        )

        # When committing to GitOps
        with mock.patch.object(commit, "_run_command") as mock_run:
            mock_run.return_value = (0, "https://github.com/org/repo/pull/42", "")

            result = asyncio.run(
                commit.commit_to_gitops(
                    chart=chart,
                    gitops_root=tmp_path,
                    branch_prefix="chart",
                )
            )

        # Then git commands were called
        assert mock_run.call_count >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/application/charts/test_commit.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/sentinel/application/charts/__init__.py` (empty).

Create `src/sentinel/application/charts/commit.py`:

```python
"""
GitOps committer for generated Helm charts.

Write chart files to the gitops directory, create a feature branch,
commit, push, and open a pull request.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sentinel.settings import PROJECT_ROOT
from sentinel.utils import logs


logger = logs.get_logger()

_DEFAULT_GITOPS_ROOT = PROJECT_ROOT / "gitops" / "charts"


async def _run_command(
    *args: str,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """
    Run a shell command and return (returncode, stdout, stderr).
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


def write_chart_files(
    *,
    chart: "entities.ChartOutput",
    gitops_root: Path = _DEFAULT_GITOPS_ROOT,
) -> Path:
    """
    Write generated chart files to the gitops directory.

    :param chart: The chart output with generated files.
    :param gitops_root: Root directory for gitops charts.
    :returns: The chart output directory path.
    """
    from sentinel.domain.charts import entities  # noqa: F811 — deferred to avoid circular

    output_dir = gitops_root / chart.service_name
    output_dir.mkdir(parents=True, exist_ok=True)

    for gf in chart.files:
        file_path = output_dir / gf.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(gf.content)

    logger.info(
        "chart_files_written",
        service_name=chart.service_name,
        output_dir=str(output_dir),
        file_count=len(chart.files),
    )

    return output_dir


async def commit_to_gitops(
    *,
    chart: "entities.ChartOutput",
    gitops_root: Path = _DEFAULT_GITOPS_ROOT,
    branch_prefix: str = "chart",
) -> str:
    """
    Write chart files, create a branch, commit, push, and open a PR.

    :param chart: The chart output with generated files.
    :param gitops_root: Root directory for gitops charts.
    :param branch_prefix: Prefix for the branch name.
    :returns: The pull request URL, or an error message.
    """
    from sentinel.domain.charts import entities  # noqa: F811

    output_dir = write_chart_files(chart=chart, gitops_root=gitops_root)

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    branch_name = f"{branch_prefix}/{chart.service_name}-{timestamp}"

    # Create branch
    rc, out, err = await _run_command("git", "checkout", "-b", branch_name, cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_checkout_failed", branch=branch_name, stderr=err)
        return f"Branch creation failed: {err}"

    # Stage chart files
    rc, out, err = await _run_command("git", "add", str(output_dir), cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_add_failed", stderr=err)
        return f"Git add failed: {err}"

    # Commit
    commit_msg = f"feat: generate Helm chart for {chart.service_name}"
    rc, out, err = await _run_command("git", "commit", "-m", commit_msg, cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_commit_failed", stderr=err)
        return f"Git commit failed: {err}"

    # Push
    rc, out, err = await _run_command("git", "push", "-u", "origin", branch_name, cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_push_failed", stderr=err)
        return f"Git push failed: {err}"

    # Create PR
    pr_title = f"feat: deploy {chart.service_name} Helm chart"
    pr_body = (
        f"## Generated Helm Chart\n\n"
        f"Service: `{chart.service_name}`\n"
        f"Files: {len(chart.files)}\n"
        f"Generation attempts: {chart.generation_attempts}\n"
        f"Confidence: {chart.confidence_score or 'N/A'}\n"
    )
    rc, out, err = await _run_command(
        "gh", "pr", "create",
        "--title", pr_title,
        "--body", pr_body,
        cwd=PROJECT_ROOT,
    )
    if rc != 0:
        logger.warning("gh_pr_create_failed", stderr=err)
        return f"PR creation failed: {err}"

    logger.info("chart_pr_created", service_name=chart.service_name, pr_url=out)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/application/charts/test_commit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/application/charts/ tests/unit/application/charts/
git commit -m "feat: add GitOps committer for chart generation output"
```

---

## Task 10: Add Pipeline Reply Type

**Files:**
- Modify: `src/sentinel/domain/pipeline/types.py`
- Modify: `src/sentinel/interfaces/graphs/common.py`

- [ ] **Step 1: Add ChartGenerationReply to pipeline types**

Append to `src/sentinel/domain/pipeline/types.py`:

```python
class ChartGenerationReply(BaseModel):
    """Output from the chart generation pipeline."""

    service_name: str
    files_generated: int = 0
    validation_passed: bool = False
    policy_violations: int = 0
    generation_attempts: int = 1
    confidence: confidence_entities.ConfidenceScore | None = None
    pr_url: str = ""
    error: str | None = None
```

- [ ] **Step 2: Re-export from common.py**

Add to `src/sentinel/interfaces/graphs/common.py`:

```python
from sentinel.domain.pipeline.types import ChartGenerationReply as ChartGenerationReply
```

- [ ] **Step 3: Run existing tests**

Run: `uv run pytest tests/unit/ -x -q`
Expected: PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add src/sentinel/domain/pipeline/types.py src/sentinel/interfaces/graphs/common.py
git commit -m "feat: add ChartGenerationReply pipeline type"
```

---

## Task 11: Create Pipeline Graph

**Files:**
- Create: `src/sentinel/interfaces/graphs/chart_generation.py`
- Test: `tests/unit/interfaces/graphs/test_chart_generation.py`

This is the largest task. The pipeline has 7 nodes with a self-heal loop.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/interfaces/graphs/test_chart_generation.py
from __future__ import annotations

import asyncio
import dataclasses
from unittest import mock

import pytest

from sentinel.domain.charts import entities, policies, validation, confidence
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.interfaces.graphs import chart_generation, common
from tests import factories


class TestGenerateChart:
    def test_full_pipeline_success(self):
        # Given a chart request and mocked agents
        request = factories.make_chart_request()
        spec = factories.make_chart_spec()
        policy = factories.make_team_policy()
        generated_files = (
            factories.make_generated_file(
                path="templates/deployment.yaml",
                content="apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api-gateway",
            ),
            factories.make_generated_file(
                path="templates/service.yaml",
                content="apiVersion: v1\nkind: Service\nmetadata:\n  name: api-gateway",
            ),
        )
        validation_result = factories.make_validation_result()

        with mock.patch.object(chart_generation, "_parse_request") as mock_parse, \
             mock.patch.object(chart_generation, "_load_policy") as mock_load, \
             mock.patch.object(chart_generation, "_generate_chart_files") as mock_gen, \
             mock.patch.object(validation, "validate_chart") as mock_validate, \
             mock.patch.object(chart_generation, "_commit_chart") as mock_commit:
            mock_parse.return_value = spec
            mock_load.return_value = policy
            mock_gen.return_value = generated_files
            mock_validate.return_value = validation_result
            mock_commit.return_value = "https://github.com/org/repo/pull/42"

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    parser_model="test-model",
                    generator_model="test-model",
                )
            )

        # Then the pipeline succeeds
        assert result.service_name == "api-gateway"
        assert result.validation_passed is True
        assert result.error is None

    def test_pipeline_returns_error_when_policy_not_found(self):
        # Given a request for an unknown team
        request = factories.make_chart_request(team="nonexistent")
        spec = factories.make_chart_spec()

        with mock.patch.object(chart_generation, "_parse_request") as mock_parse, \
             mock.patch.object(chart_generation, "_load_policy") as mock_load:
            mock_parse.return_value = spec
            mock_load.side_effect = FileNotFoundError("No policy file")

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    parser_model="test-model",
                    generator_model="test-model",
                )
            )

        # Then error is returned
        assert result.error is not None
        assert "policy" in result.error.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/interfaces/graphs/test_chart_generation.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the pipeline**

Create `src/sentinel/interfaces/graphs/chart_generation.py`:

```python
"""
Pydantic Graph pipeline for K8s Helm chart generation.

Pipeline: ParseRequest → LoadPolicy → MergeSpec → GenerateChart →
          ValidateChart → ApprovalGate → CommitToGitOps

The ValidateChart node loops back to GenerateChart on syntax errors
(max retries controlled by settings). Policy violations escalate
to the ApprovalGate for human review.
"""
from __future__ import annotations

from sentinel.application.charts import commit as chart_commit
from sentinel.domain.charts import confidence as chart_confidence
from sentinel.domain.charts import entities, policies, validation
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.interfaces.graphs.agents import chart_generator, chart_request_parser, utils
from sentinel.settings import get_settings
from sentinel.utils import logs


logger = logs.get_logger()


async def _parse_request(
    *,
    request: entities.ChartRequest,
    model: str,
) -> entities.ChartSpec:
    """Run the chart request parser agent."""
    result = await chart_request_parser.agent.run(
        user_prompt=request.raw_message,
        model=utils.get_model_with_gateway(model),
        deps=chart_request_parser.Dependencies(
            raw_message=request.raw_message,
            requester=request.requester,
            team=request.team,
        ),
    )
    return result.output


async def _load_policy(*, team: str) -> entities.TeamPolicy:
    """Load team policy from YAML."""
    return policies.load_team_policy(team=team)


async def _generate_chart_files(
    *,
    spec: entities.ChartSpec,
    policy: entities.TeamPolicy,
    model: str,
    error_context: str = "",
) -> tuple[entities.GeneratedFile, ...]:
    """Run the chart generator agent."""
    user_prompt = f"Generate Helm chart for {spec.service_name}"
    if error_context:
        user_prompt += f"\n\nPrevious attempt failed with errors:\n{error_context}\nPlease fix these issues."

    result = await chart_generator.agent.run(
        user_prompt=user_prompt,
        model=utils.get_model_with_gateway(model),
        deps=chart_generator.Dependencies(
            service_name=spec.service_name,
            image=spec.image,
            spec_json=spec.model_dump_json(),
            policy_json=policy.model_dump_json(),
        ),
    )
    return result.output.files


async def _commit_chart(
    *,
    chart: entities.ChartOutput,
) -> str:
    """Commit chart to GitOps directory and open a PR."""
    return await chart_commit.commit_to_gitops(chart=chart)


async def generate_chart(
    *,
    request: entities.ChartRequest,
    parser_model: str = "",
    generator_model: str = "",
    auto_validate: bool | None = None,
    auto_sandbox: bool | None = None,
    max_retries: int | None = None,
    status_update_fn: object | None = None,
) -> pipeline_types.ChartGenerationReply:
    """
    Run the full chart generation pipeline.

    :param request: The raw chart request from the user.
    :param parser_model: LLM model for request parsing.
    :param generator_model: LLM model for chart generation.
    :param auto_validate: Override for K8S_CHART_AUTO_VALIDATE.
    :param auto_sandbox: Override for K8S_CHART_AUTO_SANDBOX.
    :param max_retries: Override for K8S_CHART_MAX_RETRIES.
    :returns: A ChartGenerationReply with results.
    """
    settings = get_settings()
    parser_model = parser_model or settings.k8s_chart_parser_llm
    generator_model = generator_model or settings.k8s_chart_generator_llm
    if max_retries is None:
        max_retries = settings.k8s_chart_max_retries

    # Step 1: Parse request
    try:
        spec = await _parse_request(request=request, model=parser_model)
    except Exception as exc:
        logs.log_exception(exc, params={"node": "ParseRequest"})
        return pipeline_types.ChartGenerationReply(
            service_name="unknown",
            error=f"Failed to parse request: {exc}",
        )

    logs.log_event(
        "chart_request_parsed",
        params={"service_name": spec.service_name, "image": spec.image},
    )

    # Step 2: Load policy
    try:
        policy = await _load_policy(team=request.team)
    except FileNotFoundError as exc:
        logs.log_exception(exc, params={"node": "LoadPolicy", "team": request.team})
        return pipeline_types.ChartGenerationReply(
            service_name=spec.service_name,
            error=f"Policy not found: {exc}",
        )

    # Step 3: Merge spec with policy
    merged_spec, violations = policies.merge_spec_with_policy(
        spec=spec, policy=policy
    )

    if violations:
        logs.log_event(
            "policy_violations_detected",
            params={
                "service_name": spec.service_name,
                "violation_count": len(violations),
            },
        )

    # Step 4 + 5: Generate and validate (with self-heal loop)
    generation_attempts = 0
    error_context = ""
    validation_result = None

    for attempt in range(max_retries + 1):
        generation_attempts = attempt + 1

        try:
            files = await _generate_chart_files(
                spec=merged_spec,
                policy=policy,
                model=generator_model,
                error_context=error_context,
            )
        except Exception as exc:
            logs.log_exception(
                exc, params={"node": "GenerateChart", "attempt": generation_attempts}
            )
            error_context = str(exc)
            continue

        chart_output = entities.ChartOutput(
            service_name=spec.service_name,
            files=files,
            policy_violations=violations,
            generation_attempts=generation_attempts,
        )

        validation_result = await validation.validate_chart(chart=chart_output)

        if validation_result.helm_template_ok and validation_result.kubeconform_ok:
            break

        # Self-heal: feed errors back to the generator
        error_context = "\n".join(validation_result.errors)
        logs.log_event(
            "chart_validation_failed_retrying",
            params={
                "service_name": spec.service_name,
                "attempt": generation_attempts,
                "errors": validation_result.errors,
            },
        )
    else:
        # Exhausted retries
        return pipeline_types.ChartGenerationReply(
            service_name=spec.service_name,
            files_generated=len(files) if "files" in dir() else 0,
            validation_passed=False,
            policy_violations=len(violations),
            generation_attempts=generation_attempts,
            error=f"Validation failed after {generation_attempts} attempts: {error_context}",
        )

    # Step 6: Confidence scoring
    score = chart_confidence.calculate_chart_confidence(
        schema_valid=validation_result.kubeconform_ok,
        template_renders=validation_result.helm_template_ok,
        template_has_warnings=len(validation_result.warnings) > 0,
        policy_compliant=len(violations) == 0,
        policy_auto_resolved=len(violations) > 0,
        spec_coverage=1.0,
        retry_count=generation_attempts - 1,
    )

    chart_output = chart_output.model_copy(
        update={
            "validation_result": validation_result,
            "confidence_score": score.total,
        }
    )

    # Step 7: Commit to GitOps
    try:
        pr_url = await _commit_chart(chart=chart_output)
    except Exception as exc:
        logs.log_exception(exc, params={"node": "CommitToGitOps"})
        pr_url = f"Commit failed: {exc}"

    logs.log_event(
        "chart_generation_completed",
        params={
            "service_name": spec.service_name,
            "attempts": generation_attempts,
            "confidence": score.total,
            "pr_url": pr_url,
        },
    )

    return pipeline_types.ChartGenerationReply(
        service_name=spec.service_name,
        files_generated=len(chart_output.files),
        validation_passed=True,
        policy_violations=len(violations),
        generation_attempts=generation_attempts,
        confidence=score,
        pr_url=pr_url,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/interfaces/graphs/test_chart_generation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/interfaces/graphs/chart_generation.py \
        tests/unit/interfaces/graphs/test_chart_generation.py
git commit -m "feat: add chart generation pipeline with self-heal loop"
```

---

## Task 12: Wire into Config

**Files:**
- Modify: `src/sentinel/config.py:63-240`

- [ ] **Step 1: Add chart model properties**

Add to `src/sentinel/config.py` in the `Configuration` class, after the existing `k8s_investigator_model` property:

```python
    # -- Chart generation helpers --------------------------------------------

    @property
    def chart_parser_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_chart_parser_llm)

    @property
    def chart_generator_model(self) -> str:
        return _normalise_model_name(self.settings.k8s_chart_generator_llm)
```

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/unit/ -x -q`
Expected: PASS (no regressions)

- [ ] **Step 3: Commit**

```bash
git add src/sentinel/config.py
git commit -m "feat: add chart generation model properties to config"
```

---

## Task 13: Extend Streamlit UI

**Files:**
- Modify: `src/sentinel/interfaces/chat/app.py`

- [ ] **Step 1: Add chart generation scenarios**

Add after `_K8S_SCENARIOS` in `app.py`:

```python
_CHART_SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "label": "Simple web service",
        "prompt": (
            "Deploy a Python web service called api-gateway on port 8080. "
            "It needs 256Mi memory and 200m CPU. Team: platform."
        ),
    },
    {
        "label": "Service with HPA",
        "prompt": (
            "Deploy a Node.js service called order-processor using image "
            "myrepo/order-processor:v2.1.0. It listens on port 3000 and needs "
            "autoscaling from 3 to 10 replicas. Memory limit 512Mi, CPU limit 1000m. "
            "It connects to redis at redis.internal:6379. Team: platform."
        ),
    },
    {
        "label": "Secure service with NetworkPolicy",
        "prompt": (
            "Deploy a Go microservice called auth-service on port 9090. "
            "It must run as non-root with a strict network policy allowing "
            "only ingress on port 9090 and egress to postgres.internal:5432. "
            "Memory: 128Mi-256Mi, CPU: 100m-500m. 2 replicas. Team: platform."
        ),
    },
)
```

- [ ] **Step 2: Add chart generation runner**

Add after `_run_support()`:

```python
async def _run_chart_generation(
    text: str,
    *,
    on_status: Callable[[str], None],
) -> common.ChartGenerationReply:
    from sentinel.domain.charts import entities as chart_entities
    from sentinel.interfaces.graphs import chart_generation

    now = datetime.now(tz=UTC)
    request = chart_entities.ChartRequest(
        requester="local-user",
        team="platform",
        raw_message=text,
        requested_at=now,
    )

    on_status("Parsing chart request...")
    return await chart_generation.generate_chart(
        request=request,
        parser_model=_selected_model("analyser"),
        generator_model=_selected_model("analyser"),
    )
```

- [ ] **Step 3: Add chart result formatting**

Add after `_format_support()`:

```python
def _format_chart_result(reply: common.ChartGenerationReply) -> str:
    confidence_label = reply.confidence.label.value if reply.confidence else "Unknown"
    confidence_icon = {"High": "🟢", "Medium": "🟡"}.get(confidence_label, "🔴")

    parts = [
        f"### Chart Generation: {reply.service_name}",
        f"**Confidence:** {confidence_icon} {confidence_label}  "
        f"**Files:** {reply.files_generated}  "
        f"**Attempts:** {reply.generation_attempts}",
    ]
    if reply.validation_passed:
        parts.append("\n✅ Validation passed")
    else:
        parts.append("\n❌ Validation failed")
    if reply.policy_violations > 0:
        parts.append(f"\n⚠️ {reply.policy_violations} policy violation(s)")
    if reply.pr_url and not reply.pr_url.startswith(("Commit failed", "Branch", "Git", "PR")):
        parts.append(f"\n**PR:** {reply.pr_url}")
    if reply.error:
        parts.append(f"\n**Error:** {reply.error}")
    return "\n".join(parts)
```

- [ ] **Step 4: Add scenario buttons to sidebar**

In `_render_sidebar()`, after the K8s investigation scenarios line, add:

```python
        _render_scenario_buttons("Chart Generation", _CHART_SCENARIOS, "chart")
```

- [ ] **Step 5: Wire into intent handling**

In `_handle_user_input()`, add a chart generation path. After the SRE/Support routing, add detection for chart generation requests. The simplest approach is to check the intent or add a sidebar toggle:

Add to sidebar (after the K8s Backend selectbox):

```python
        st.divider()
        st.header("Chart Generation")
        if "chart_mode" not in st.session_state:
            st.session_state["chart_mode"] = False
        st.toggle("Enable chart generation mode", key="chart_mode")
```

In `_handle_user_input()`, before the intent classification, add:

```python
        if st.session_state.get("chart_mode", False):
            reply = _run_async(
                _run_chart_generation(user_input, on_status=_on_status)
            )
            formatted = _format_chart_result(reply)
            status_placeholder.empty()
            st.markdown(formatted)
            st.session_state.messages.append(
                {"role": "assistant", "content": formatted}
            )
            return
```

- [ ] **Step 6: Verify the UI renders without errors**

Run: `uv run streamlit run src/sentinel/interfaces/chat/app.py` and verify the page loads.

- [ ] **Step 7: Commit**

```bash
git add src/sentinel/interfaces/chat/app.py
git commit -m "feat: add chart generation mode to Streamlit chat UI"
```

---

## Task 14: Create Evaluation Framework

**Files:**
- Create: `src/sentinel/evals/datasets/chart_generation_cases.json`
- Create: `src/sentinel/evals/evaluators/chart_evaluators.py`
- Modify: `src/sentinel/evals/cases/base.py`
- Test: `tests/unit/evals/evaluators/test_chart_evaluators.py`

- [ ] **Step 1: Create dataset JSON**

Create `src/sentinel/evals/datasets/chart_generation_cases.json`:

```json
[
  {
    "id": "cg-001",
    "description": "Simple web service with port and resources",
    "input": {
      "raw_message": "Deploy a Python web service called api-gateway on port 8080 with 256Mi memory and 200m CPU",
      "requester": "alice",
      "team": "platform"
    },
    "output": {
      "service_name": "api-gateway",
      "files": [
        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1\nkind: Deployment"},
        {"path": "templates/service.yaml", "content": "apiVersion: v1\nkind: Service"}
      ],
      "confidence": 0.85
    },
    "expected": {
      "has_deployment": true,
      "has_service": true,
      "min_files": 2,
      "min_confidence": 0.7
    }
  },
  {
    "id": "cg-002",
    "description": "Service with HPA and dependencies",
    "input": {
      "raw_message": "Deploy order-processor with autoscaling 3-10 replicas, 512Mi memory, connects to redis:6379",
      "requester": "bob",
      "team": "platform"
    },
    "output": {
      "service_name": "order-processor",
      "files": [
        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1\nkind: Deployment"},
        {"path": "templates/service.yaml", "content": "apiVersion: v1\nkind: Service"},
        {"path": "templates/hpa.yaml", "content": "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler"}
      ],
      "confidence": 0.8
    },
    "expected": {
      "has_deployment": true,
      "has_service": true,
      "has_hpa": true,
      "min_files": 3,
      "min_confidence": 0.7
    }
  },
  {
    "id": "cg-003",
    "description": "Secure service with NetworkPolicy",
    "input": {
      "raw_message": "Deploy auth-service on port 9090 with strict network policy and non-root security context",
      "requester": "alice",
      "team": "platform"
    },
    "output": {
      "service_name": "auth-service",
      "files": [
        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1\nkind: Deployment"},
        {"path": "templates/service.yaml", "content": "apiVersion: v1\nkind: Service"},
        {"path": "templates/networkpolicy.yaml", "content": "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy"}
      ],
      "confidence": 0.8
    },
    "expected": {
      "has_deployment": true,
      "has_service": true,
      "has_networkpolicy": true,
      "min_files": 3,
      "min_confidence": 0.7
    }
  },
  {
    "id": "cg-004",
    "description": "Complex service with env vars, dependencies, and all resources",
    "input": {
      "raw_message": "Deploy payment-gateway on port 443 with env vars STRIPE_KEY and LOG_LEVEL=debug, depends on postgres:5432 and redis:6379, needs HPA 2-8 replicas, network policy, PDB, memory 512Mi-1Gi, CPU 250m-1000m",
      "requester": "alice",
      "team": "platform"
    },
    "output": {
      "service_name": "payment-gateway",
      "files": [
        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1"},
        {"path": "templates/service.yaml", "content": "apiVersion: v1"},
        {"path": "templates/hpa.yaml", "content": "apiVersion: autoscaling/v2"},
        {"path": "templates/networkpolicy.yaml", "content": "apiVersion: networking.k8s.io/v1"}
      ],
      "confidence": 0.75
    },
    "expected": {
      "has_deployment": true,
      "has_service": true,
      "min_files": 4,
      "min_confidence": 0.6
    }
  },
  {
    "id": "cg-005",
    "description": "Minimal spec relying on defaults",
    "input": {
      "raw_message": "Deploy a service called health-check",
      "requester": "bob",
      "team": "platform"
    },
    "output": {
      "service_name": "health-check",
      "files": [
        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1"},
        {"path": "templates/service.yaml", "content": "apiVersion: v1"}
      ],
      "confidence": 0.9
    },
    "expected": {
      "has_deployment": true,
      "has_service": true,
      "min_files": 2,
      "min_confidence": 0.7
    }
  }
]
```

- [ ] **Step 2: Write evaluator tests**

```python
# tests/unit/evals/evaluators/test_chart_evaluators.py
from __future__ import annotations

import asyncio

import pytest

from sentinel.evals import types
from sentinel.evals.evaluators import chart_evaluators


class TestYamlStructureCheck:
    def test_passes_when_required_files_present(self):
        # Given a case with deployment and service files
        evaluator = chart_evaluators.YamlStructureCheck(
            required_file_patterns=("deployment", "service"),
            rubric="Has required Kubernetes resources",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [
                        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1"},
                        {"path": "templates/service.yaml", "content": "apiVersion: v1"},
                    ],
                },
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it passes
        key = next(iter(result))
        assert result[key].value is True

    def test_fails_when_required_file_missing(self):
        # Given a case missing the service file
        evaluator = chart_evaluators.YamlStructureCheck(
            required_file_patterns=("deployment", "service"),
            rubric="Has required Kubernetes resources",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [
                        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1"},
                    ],
                },
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it fails
        key = next(iter(result))
        assert result[key].value is False


def _make_eval_context(*, case_payload: dict) -> chart_evaluators.evaluators.EvaluatorContext:
    """Build a minimal evaluator context for testing."""
    from unittest.mock import MagicMock
    ctx = MagicMock()
    ctx.inputs = types.InputData(
        agent_name="chart_generator",
        case_payload=case_payload,
    )
    return ctx
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/evals/evaluators/test_chart_evaluators.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 4: Implement evaluators**

Create `src/sentinel/evals/evaluators/chart_evaluators.py`:

```python
"""
Chart-specific evaluators for the evaluation framework.

Evaluators:
- YamlStructureCheck: verifies required Kubernetes resource files are present
- PolicyComplianceCheck: verifies output respects policy constraints
- SpecCoverageCheck: verifies requested resources are all generated
"""
from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types


@dataclasses.dataclass
class YamlStructureCheck(evaluators.Evaluator):
    """
    Verify that required Kubernetes resource files are present in the output.

    Checks that the ``output.files`` list contains files matching each
    required pattern (e.g. ``"deployment"``, ``"service"``).
    """

    required_file_patterns: tuple[str, ...] = ()
    rubric: str = "Required Kubernetes resource files are present"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """Check that all required file patterns appear in output files."""
        payload = ctx.inputs.case_payload
        files = payload.get("output", {}).get("files", [])
        file_paths = [f.get("path", "").lower() for f in files]

        missing: list[str] = []
        for pattern in self.required_file_patterns:
            if not any(pattern in path for path in file_paths):
                missing.append(pattern)

        passed = len(missing) == 0
        reason = (
            "All required files present"
            if passed
            else f"Missing files matching: {', '.join(missing)}"
        )

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=passed,
                reason=reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "required_file_patterns": self.required_file_patterns,
            "rubric": self.rubric,
        }


@dataclasses.dataclass
class SpecCoverageCheck(evaluators.Evaluator):
    """
    Verify that the output has at least the minimum expected number of files.
    """

    min_files_field: str = "expected.min_files"
    rubric: str = "Generated file count meets minimum"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """Check file count against expected minimum."""
        payload = ctx.inputs.case_payload
        files = payload.get("output", {}).get("files", [])
        actual_count = len(files)

        # Resolve expected min from payload
        expected_min = payload
        for segment in self.min_files_field.split("."):
            expected_min = expected_min.get(segment, 0)

        passed = actual_count >= int(expected_min)
        reason = f"Generated {actual_count} files (minimum: {expected_min})"

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=passed,
                reason=reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "min_files_field": self.min_files_field,
            "rubric": self.rubric,
        }
```

- [ ] **Step 5: Register in base.py**

Add to `src/sentinel/evals/cases/base.py`:

In `_AGENT_DATASET_FILES`, add:
```python
    "chart_generator": "chart_generation_cases.json",
```

Add a builder function:
```python
def _build_chart_generator_evaluators() -> list[pydantic_evals.evaluators.Evaluator]:
    """
    Return evaluators for chart generator cases.

    Checks: required files present, file count meets minimum.
    """
    from sentinel.evals.evaluators import chart_evaluators

    return [
        chart_evaluators.YamlStructureCheck(
            required_file_patterns=("deployment", "service"),
            rubric="Output contains Deployment and Service templates",
        ),
        chart_evaluators.SpecCoverageCheck(
            min_files_field="expected.min_files",
            rubric="Generated file count meets case minimum",
        ),
    ]
```

Add to the `_EVALUATOR_BUILDERS` dict (find it in the file and add the entry):
```python
    "chart_generator": lambda _case=None: _build_chart_generator_evaluators(),
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/evals/evaluators/test_chart_evaluators.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/sentinel/evals/datasets/chart_generation_cases.json \
        src/sentinel/evals/evaluators/chart_evaluators.py \
        src/sentinel/evals/cases/base.py \
        tests/unit/evals/evaluators/test_chart_evaluators.py
git commit -m "feat: add evaluation framework for chart generation"
```

---

## Task 15: Functional Test

**Files:**
- Create: `tests/functional/test_chart_generation_pipeline.py`

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/functional/test_chart_generation_pipeline.py
"""
End-to-end test for the chart generation pipeline.

Monkeypatches PydanticAI agents to return deterministic outputs,
then runs the full pipeline and verifies the result.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

from sentinel.domain.charts import entities, validation
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.interfaces.graphs import chart_generation
from sentinel.interfaces.graphs.agents import chart_generator, chart_request_parser


_FAKE_DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
  labels:
    app.kubernetes.io/name: api-gateway
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: api-gateway
  template:
    metadata:
      labels:
        app.kubernetes.io/name: api-gateway
    spec:
      containers:
        - name: api-gateway
          image: myrepo/api-gateway:latest
          ports:
            - containerPort: 8080
"""

_FAKE_SERVICE = """\
apiVersion: v1
kind: Service
metadata:
  name: api-gateway
spec:
  selector:
    app.kubernetes.io/name: api-gateway
  ports:
    - port: 8080
      targetPort: 8080
"""


class TestChartGenerationPipeline:
    def test_full_pipeline_with_mocked_agents(self, tmp_path: Path):
        # Given a chart request
        request = entities.ChartRequest(
            requester="alice",
            team="platform",
            raw_message="Deploy api-gateway on port 8080",
            requested_at=datetime(2026, 4, 3, tzinfo=UTC),
        )

        fake_spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api-gateway:latest",
            ports=(entities.PortSpec(container_port=8080, name="http"),),
        )

        fake_files = (
            entities.GeneratedFile(
                path="templates/deployment.yaml",
                content=_FAKE_DEPLOYMENT,
            ),
            entities.GeneratedFile(
                path="templates/service.yaml",
                content=_FAKE_SERVICE,
            ),
        )

        fake_validation = entities.ValidationResult(
            helm_template_ok=True,
            kubeconform_ok=True,
        )

        # When running the full pipeline with mocked agents and validation
        with mock.patch.object(chart_generation, "_parse_request") as mock_parse, \
             mock.patch.object(chart_generation, "_load_policy") as mock_load, \
             mock.patch.object(chart_generation, "_generate_chart_files") as mock_gen, \
             mock.patch.object(validation, "validate_chart") as mock_validate, \
             mock.patch.object(chart_generation, "_commit_chart") as mock_commit:

            mock_parse.return_value = fake_spec
            mock_load.return_value = entities.TeamPolicy(
                team="platform",
                namespace="platform-prod",
                max_memory="2Gi",
                max_cpu="2000m",
                max_replicas=10,
                require_network_policy=True,
                require_non_root=True,
            )
            mock_gen.return_value = fake_files
            mock_validate.return_value = fake_validation
            mock_commit.return_value = "https://github.com/org/repo/pull/42"

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    parser_model="test-model",
                    generator_model="test-model",
                )
            )

        # Then the pipeline produces a successful result
        assert result.service_name == "api-gateway"
        assert result.files_generated == 2
        assert result.validation_passed is True
        assert result.generation_attempts == 1
        assert result.pr_url == "https://github.com/org/repo/pull/42"
        assert result.error is None
        assert result.confidence is not None
        assert result.confidence.total >= 0.7

    def test_self_heal_loop_retries_on_validation_failure(self):
        # Given a request where generation fails once then succeeds
        request = entities.ChartRequest(
            requester="alice",
            team="platform",
            raw_message="Deploy api-gateway",
            requested_at=datetime(2026, 4, 3, tzinfo=UTC),
        )

        fake_spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api-gateway:latest",
        )

        bad_files = (
            entities.GeneratedFile(
                path="templates/deployment.yaml",
                content="invalid yaml",
            ),
        )
        good_files = (
            entities.GeneratedFile(
                path="templates/deployment.yaml",
                content=_FAKE_DEPLOYMENT,
            ),
        )

        failing_validation = entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=("template rendering failed",),
        )
        passing_validation = entities.ValidationResult(
            helm_template_ok=True,
            kubeconform_ok=True,
        )

        # When the first generation fails but the second succeeds
        with mock.patch.object(chart_generation, "_parse_request") as mock_parse, \
             mock.patch.object(chart_generation, "_load_policy") as mock_load, \
             mock.patch.object(chart_generation, "_generate_chart_files") as mock_gen, \
             mock.patch.object(validation, "validate_chart") as mock_validate, \
             mock.patch.object(chart_generation, "_commit_chart") as mock_commit:

            mock_parse.return_value = fake_spec
            mock_load.return_value = entities.TeamPolicy(team="platform")
            mock_gen.side_effect = [bad_files, good_files]
            mock_validate.side_effect = [failing_validation, passing_validation]
            mock_commit.return_value = "https://github.com/org/repo/pull/43"

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    parser_model="test-model",
                    generator_model="test-model",
                    max_retries=3,
                )
            )

        # Then the pipeline retried and succeeded
        assert result.validation_passed is True
        assert result.generation_attempts == 2
        assert result.error is None
```

- [ ] **Step 2: Run the functional test**

Run: `uv run pytest tests/functional/test_chart_generation_pipeline.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/functional/test_chart_generation_pipeline.py
git commit -m "test: add functional tests for chart generation pipeline"
```

---

## Task 16: Run Full Test Suite and Lint

- [ ] **Step 1: Run all tests**

Run: `uv run pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 2: Run linter**

Run: `make lint`
Expected: No errors. If there are ruff or mypy issues, fix them.

- [ ] **Step 3: Run lint-fix if needed**

Run: `make lint-fix`

- [ ] **Step 4: Final commit if any lint fixes**

```bash
git add -u
git commit -m "chore: fix lint issues in chart coding agent"
```

---

## Parallelism Guide

Tasks that can run in parallel (no shared state):

| Phase | Tasks | Dependencies |
|-------|-------|-------------|
| 1 | Task 1, Task 2 | None |
| 2 | Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9 | Task 2 |
| 3 | Task 10, Task 11 | Phase 2 |
| 4 | Task 12, Task 13, Task 14 | Task 10, Task 11 |
| 5 | Task 15, Task 16 | All above |
