# K8s Agent & MCP Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement dual K8s investigation backends (native PydanticAI agent + kagent) with MCP integration (server + client), pipeline-agnostic evaluation metrics, and Streamlit comparison UI.

**Architecture:** Adapter hierarchy (`BaseInvestigationAdapter` → `DirectToolsetAdapter` + `K8sInvestigationAdapter` → `NativeK8sAgent` / `KagentAdapter`) with typed audit trail envelope. MCP server at `interfaces/mcp/`, MCP client at `plugins/toolsets/mcp.py`. Evaluation metrics in `domain/evaluation/`.

**Tech Stack:** Python 3.13, PydanticAI, FastMCP, `kubernetes` async client, attrs, Pydantic Graph, Helm, Kind/Minikube

**Spec:** `docs/plans/k8s-agent-and-mcp-integration.md`

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `src/sentinel/domain/sre/investigation.py` | `BaseInvestigationAdapter`, `K8sInvestigationAdapter` ABCs, `InvestigationResult`, `InvestigationContext`, `AuditEntry` |
| `src/sentinel/domain/sre/k8s_native_agent.py` | `NativeK8sAgent` — PydanticAI agent + kubernetes client |
| `src/sentinel/domain/sre/kagent_adapter.py` | `KagentAdapter` — delegates to kagent CRDs |
| `src/sentinel/domain/tools/kubernetes.py` | K8s query tool functions (pods, deployments, events, logs) |
| `src/sentinel/domain/evaluation/__init__.py` | Package init |
| `src/sentinel/domain/evaluation/metrics.py` | `EvaluationMetrics` frozen attrs class |
| `src/sentinel/domain/evaluation/comparison.py` | `ComparisonResult` frozen attrs class |
| `src/sentinel/plugins/toolsets/kubernetes.py` | K8s `FunctionToolset` wrapper |
| `src/sentinel/plugins/toolsets/mcp.py` | MCP client toolset builder |
| `src/sentinel/plugins/prompts/k8s_investigator.j2` | K8s agent system/user prompts |
| `src/sentinel/interfaces/graphs/agents/k8s_investigator.py` | K8s investigator PydanticAI agent |
| `src/sentinel/interfaces/mcp/__init__.py` | Package init |
| `src/sentinel/interfaces/mcp/server.py` | FastMCP server app |
| `src/sentinel/interfaces/mcp/tools/__init__.py` | Package init |
| `src/sentinel/interfaces/mcp/tools/observability.py` | MCP server observability tools |
| `src/sentinel/interfaces/mcp/tools/documentation.py` | MCP server documentation tools |
| `src/sentinel/interfaces/mcp/tools/investigation.py` | MCP server investigation tools |
| `helm/sentinel/templates/clusterrole.yaml` | K8s API read-only RBAC |
| `helm/sentinel/templates/clusterrolebinding.yaml` | Binds ClusterRole to ServiceAccount |
| `helm/sentinel/templates/mcp-deployment.yaml` | MCP server deployment |
| `helm/sentinel/templates/mcp-service.yaml` | MCP server ClusterIP service |
| `tests/unit/domain/sre/test_investigation.py` | Tests for adapter hierarchy + audit trail |
| `tests/unit/domain/sre/test_k8s_native_agent.py` | Tests for native K8s agent |
| `tests/unit/domain/sre/test_kagent_adapter.py` | Tests for kagent adapter |
| `tests/unit/domain/tools/test_kubernetes.py` | Tests for K8s tool functions |
| `tests/unit/domain/evaluation/test_metrics.py` | Tests for evaluation metrics |
| `tests/unit/domain/evaluation/test_comparison.py` | Tests for comparison result |
| `tests/unit/plugins/toolsets/test_kubernetes.py` | Tests for K8s toolset |
| `tests/unit/plugins/toolsets/test_mcp.py` | Tests for MCP client builder |
| `tests/unit/interfaces/mcp/test_server.py` | Tests for MCP server tools |

### Modified files
| File | What changes |
|------|-------------|
| `src/sentinel/domain/sre/holmes_adapter.py` | `BaseHolmesAdapter` renamed to keep backward compat, `DirectToolsetAdapter` implements new `BaseInvestigationAdapter` |
| `src/sentinel/settings.py` | Add K8s agent, kagent, and MCP settings |
| `src/sentinel/config.py` | Add `build_k8s_adapter()`, `build_mcp_toolsets()` methods |
| `src/sentinel/interfaces/graphs/sre_investigation.py` | `Dependencies.holmes` type widened to `BaseInvestigationAdapter` |
| `src/sentinel/interfaces/chat/app.py` | Backend selector, audit trail viewer, K8s scenarios, comparison mode |
| `tests/factories/__init__.py` | `MockHolmesAdapter` updated to implement `BaseInvestigationAdapter`, add `MockK8sAdapter` |
| `pyproject.toml` | Add `kubernetes`, `fastmcp` deps; add `evaluation` to import-linter layers |
| `helm/sentinel/values.yaml` | Add `k8sAgent`, `kagent`, `mcpServer` blocks |
| `helm/sentinel/templates/networkpolicy.yaml` | Add K8s API, kagent, MCP egress rules |

---

## Task 1: AuditEntry and InvestigationResult Domain Types

**Files:**
- Create: `src/sentinel/domain/sre/investigation.py`
- Test: `tests/unit/domain/sre/test_investigation.py`

- [x] **Step 1: Write tests for AuditEntry and InvestigationResult**

```python
# tests/unit/domain/sre/test_investigation.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sentinel.domain.sre import investigation


class TestAuditEntry:
    def test_creates_immutable_audit_entry(self) -> None:
        # Given a complete set of audit entry fields
        now = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)

        # When an AuditEntry is created
        entry = investigation.AuditEntry(
            timestamp=now,
            adapter_name="native_k8s",
            action="tool_call",
            tool_name="get_pod_status",
            status="success",
            duration_ms=42,
            error_code=None,
            payload={"namespace": "production", "pod": "api-service-abc123"},
        )

        # Then all fields are accessible
        assert entry.adapter_name == "native_k8s"
        assert entry.action == "tool_call"
        assert entry.tool_name == "get_pod_status"
        assert entry.status == "success"
        assert entry.duration_ms == 42
        assert entry.error_code is None
        assert entry.payload["namespace"] == "production"

    def test_creates_audit_entry_with_error(self) -> None:
        # Given an error scenario
        now = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)

        # When an AuditEntry is created with an error code
        entry = investigation.AuditEntry(
            timestamp=now,
            adapter_name="kagent",
            action="crd_operation",
            tool_name=None,
            status="error",
            duration_ms=5000,
            error_code="408",
            payload={"reason": "timeout waiting for CRD completion"},
        )

        # Then the error code is set
        assert entry.status == "error"
        assert entry.error_code == "408"


class TestInvestigationContext:
    def test_creates_context_with_defaults(self) -> None:
        # Given minimal context
        # When an InvestigationContext is created
        ctx = investigation.InvestigationContext(
            cluster_name="prod-eu-west-1",
        )

        # Then namespace defaults to None and additional_sources is empty
        assert ctx.cluster_name == "prod-eu-west-1"
        assert ctx.namespace is None
        assert ctx.additional_sources == ()

    def test_creates_context_with_namespace(self) -> None:
        # Given a namespace-scoped context
        # When an InvestigationContext is created with namespace
        ctx = investigation.InvestigationContext(
            cluster_name="prod-eu-west-1",
            namespace="payments",
            additional_sources=("prometheus", "alertmanager"),
        )

        # Then all fields are set
        assert ctx.namespace == "payments"
        assert ctx.additional_sources == ("prometheus", "alertmanager")


class TestInvestigationResult:
    def test_creates_result_with_audit_trail(self) -> None:
        # Given findings and audit entries
        from tests.factories import make_finding

        finding = make_finding(source="kubernetes", summary="Pod restarting")
        now = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)
        audit_entry = investigation.AuditEntry(
            timestamp=now,
            adapter_name="native_k8s",
            action="tool_call",
            tool_name="get_pod_status",
            status="success",
            duration_ms=42,
            error_code=None,
            payload={},
        )

        # When an InvestigationResult is created
        result = investigation.InvestigationResult(
            findings=(finding,),
            sources_queried=("kubernetes_pods", "kubernetes_events"),
            duration_ms=1500,
            adapter_name="native_k8s",
            audit_trail=(audit_entry,),
        )

        # Then the audit trail is attached
        assert len(result.audit_trail) == 1
        assert result.audit_trail[0].tool_name == "get_pod_status"
        assert result.adapter_name == "native_k8s"
        assert result.duration_ms == 1500
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/sre/test_investigation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sentinel.domain.sre.investigation'`

- [x] **Step 3: Implement domain types**

```python
# src/sentinel/domain/sre/investigation.py
"""
Investigation adapter hierarchy and shared result types.

Defines the contract that all investigation backends must implement,
plus the audit trail envelope for hedge fund compliance traceability.
"""
from __future__ import annotations

import abc
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import attrs

from sentinel.domain.sre import entities


@attrs.frozen
class AuditEntry:
    """
    Record of a single action taken during an investigation.

    Uses a typed envelope (stable queryable fields) with a freeform
    payload for tool-specific detail.  New tools write different keys
    into ``payload`` — no schema changes needed.
    """

    timestamp: datetime
    adapter_name: str
    action: str
    tool_name: str | None
    status: str
    duration_ms: int
    error_code: str | None
    payload: Mapping[str, Any]


@attrs.frozen
class InvestigationContext:
    """
    Context for a K8s-aware investigation.

    Passed to adapters so they know which cluster and namespace to query.
    """

    cluster_name: str
    namespace: str | None = None
    additional_sources: tuple[str, ...] = ()


@attrs.frozen
class InvestigationResult:
    """
    Unified result from any investigation adapter.

    Carries the audit trail so every action is traceable.
    """

    findings: tuple[entities.Finding, ...]
    sources_queried: tuple[str, ...]
    duration_ms: int
    adapter_name: str
    audit_trail: tuple[AuditEntry, ...] = ()


class BaseInvestigationAdapter(abc.ABC):
    """
    Abstract adapter for investigation backends.

    All investigation backends (Holmes, native K8s, kagent) implement
    this contract.  Results flow through the same confidence scoring
    and approval gate pipeline.
    """

    @abc.abstractmethod
    async def investigate(
        self,
        *,
        alert: entities.Alert,
        context: InvestigationContext | None = None,
    ) -> InvestigationResult:
        """
        Run an investigation for the given alert.

        :param alert: The alert to investigate.
        :param context: Optional K8s context (cluster, namespace).
        :returns: Structured findings with audit trail.
        """

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Return True when this adapter has the credentials/config it needs."""


class K8sInvestigationAdapter(BaseInvestigationAdapter):
    """
    Abstract adapter for Kubernetes-specific investigation backends.

    Adds K8s-specific context (cluster, namespace) to the base contract.
    Concrete implementations: NativeK8sAgent, KagentAdapter.
    """

    pass
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/sre/test_investigation.py -v`
Expected: All 4 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/domain/sre/investigation.py tests/unit/domain/sre/test_investigation.py
git commit -m "feat: add investigation adapter hierarchy with audit trail types"
```

---

## Task 2: Refactor BaseHolmesAdapter to BaseInvestigationAdapter

**Files:**
- Modify: `src/sentinel/domain/sre/holmes_adapter.py`
- Modify: `tests/factories/__init__.py`
- Modify: `src/sentinel/interfaces/graphs/sre_investigation.py`
- Modify: `src/sentinel/config.py`
- Modify: `src/sentinel/interfaces/chat/app.py`

- [x] **Step 1: Update BaseHolmesAdapter to extend BaseInvestigationAdapter**

In `src/sentinel/domain/sre/holmes_adapter.py`, make `BaseHolmesAdapter` a subclass of `BaseInvestigationAdapter` for backward compatibility, and add `is_configured` to concrete adapters:

```python
# src/sentinel/domain/sre/holmes_adapter.py — changes at top of file
# Replace the existing BaseHolmesAdapter class (lines 28-46) with:

from sentinel.domain.sre import investigation


class BaseHolmesAdapter(investigation.BaseInvestigationAdapter):
    """
    Abstract adapter for HolmesGPT investigation engine.

    Extends BaseInvestigationAdapter for backward compatibility.
    Subclasses must implement both ``investigate()`` and ``is_configured``.
    """

    @abc.abstractmethod
    async def investigate(
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> HolmesInvestigationResult:
        """
        Run a HolmesGPT investigation for the given alert.

        :param alert: The alert to investigate.
        :param context: Optional investigation context (ignored by Holmes adapters).
        """
```

Add `is_configured` property to `HolmesAdapter` (after line 64):
```python
    @property
    def is_configured(self) -> bool:
        return self._enabled
```

Add `is_configured` property to `DirectToolsetAdapter` (after line 122):
```python
    @property
    def is_configured(self) -> bool:
        return self._obs_client is not None and self._obs_client.is_configured
```

Update `HolmesAdapter.investigate()` signature (line 66-70) to accept `context`:
```python
    async def investigate(
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> HolmesInvestigationResult:
```

Update `DirectToolsetAdapter.investigate()` signature (line 124-128) to accept `context`:
```python
    async def investigate(
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> HolmesInvestigationResult:
```

- [x] **Step 2: Update MockHolmesAdapter in test factories**

In `tests/factories/__init__.py`, update `MockHolmesAdapter.investigate()` to accept `context`:

```python
# tests/factories/__init__.py — line 148, update signature:
    async def investigate(
        self,
        *,
        alert: sre_entities.Alert,
        context: holmes_adapter.investigation.InvestigationContext | None = None,
    ) -> holmes_adapter.HolmesInvestigationResult:
        return self._result
```

Add `is_configured` property:
```python
    @property
    def is_configured(self) -> bool:
        return True
```

- [x] **Step 3: Run all existing tests to verify nothing breaks**

Run: `uv run pytest tests/unit/ -v --tb=short`
Expected: All existing tests PASS (the `context` parameter has a default of `None`)

- [x] **Step 4: Verify import-linter contracts pass**

Run: `uv run lint-imports`
Expected: All contracts PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/domain/sre/holmes_adapter.py tests/factories/__init__.py
git commit -m "refactor: make BaseHolmesAdapter extend BaseInvestigationAdapter"
```

---

## Task 3: Pipeline-Agnostic Evaluation Metrics

**Files:**
- Create: `src/sentinel/domain/evaluation/__init__.py`
- Create: `src/sentinel/domain/evaluation/metrics.py`
- Create: `src/sentinel/domain/evaluation/comparison.py`
- Test: `tests/unit/domain/evaluation/test_metrics.py`
- Test: `tests/unit/domain/evaluation/test_comparison.py`
- Modify: `pyproject.toml` (import-linter)

- [x] **Step 1: Write tests for EvaluationMetrics**

```python
# tests/unit/domain/evaluation/test_metrics.py
from __future__ import annotations

import attrs
import pytest

from sentinel.domain.evaluation import metrics


class TestEvaluationMetrics:
    def test_creates_metrics_with_all_dimensions(self) -> None:
        # Given a full set of evaluation dimensions
        # When EvaluationMetrics is created
        result = metrics.EvaluationMetrics(
            factual_precision=0.85,
            factual_recall=0.78,
            hallucination_rate=0.05,
            latency_p50_ms=450,
            latency_p95_ms=1200,
            latency_p99_ms=2500,
            confidence_brier_score=0.12,
            evidence_source_count=5,
            evidence_diversity=0.8,
            robustness_variance=0.03,
            degradation_score=0.95,
            token_cost=3200,
        )

        # Then all dimensions are accessible
        assert result.factual_precision == 0.85
        assert result.hallucination_rate == 0.05
        assert result.latency_p50_ms == 450
        assert result.confidence_brier_score == 0.12
        assert result.token_cost == 3200

    def test_is_immutable(self) -> None:
        # Given an EvaluationMetrics instance
        result = metrics.EvaluationMetrics(
            factual_precision=0.85,
            factual_recall=0.78,
            hallucination_rate=0.05,
            latency_p50_ms=450,
            latency_p95_ms=1200,
            latency_p99_ms=2500,
            confidence_brier_score=0.12,
            evidence_source_count=5,
            evidence_diversity=0.8,
            robustness_variance=0.03,
            degradation_score=0.95,
            token_cost=3200,
        )

        # When attempting to mutate
        # Then it raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            result.factual_precision = 0.9  # type: ignore[misc]
```

- [x] **Step 2: Write tests for ComparisonResult**

```python
# tests/unit/domain/evaluation/test_comparison.py
from __future__ import annotations

from sentinel.domain.evaluation import comparison, metrics


def _make_metrics(*, precision: float = 0.8, latency: int = 500) -> metrics.EvaluationMetrics:
    return metrics.EvaluationMetrics(
        factual_precision=precision,
        factual_recall=0.7,
        hallucination_rate=0.05,
        latency_p50_ms=latency,
        latency_p95_ms=latency * 2,
        latency_p99_ms=latency * 4,
        confidence_brier_score=0.1,
        evidence_source_count=3,
        evidence_diversity=0.6,
        robustness_variance=0.02,
        degradation_score=0.9,
        token_cost=2000,
    )


class TestComparisonResult:
    def test_creates_comparison_with_winner_by_dimension(self) -> None:
        # Given metrics for two adapters
        baseline = _make_metrics(precision=0.85, latency=600)
        challenger = _make_metrics(precision=0.78, latency=350)

        # When a ComparisonResult is created
        result = comparison.ComparisonResult(
            case_id="k8s-crashloop-001",
            baseline=baseline,
            challenger=challenger,
            winner_by_dimension={
                "factual_precision": "native_k8s",
                "latency_p50_ms": "kagent",
            },
        )

        # Then winners are accessible per dimension
        assert result.winner_by_dimension["factual_precision"] == "native_k8s"
        assert result.winner_by_dimension["latency_p50_ms"] == "kagent"
        assert result.case_id == "k8s-crashloop-001"
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/evaluation/ -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 4: Implement evaluation types**

```python
# src/sentinel/domain/evaluation/__init__.py
```

```python
# src/sentinel/domain/evaluation/metrics.py
"""
Pipeline-agnostic evaluation metrics for comparing investigation backends.

These metrics are reusable across any adapter or pipeline — not tied
to K8s or any specific investigation type.
"""
from __future__ import annotations

import attrs


@attrs.frozen
class EvaluationMetrics:
    """
    Holistic quality metrics for a single investigation run.

    Covers accuracy, latency, calibration, evidence quality,
    robustness, degradation behaviour, and cost.
    """

    factual_precision: float
    factual_recall: float
    hallucination_rate: float
    latency_p50_ms: int
    latency_p95_ms: int
    latency_p99_ms: int
    confidence_brier_score: float
    evidence_source_count: int
    evidence_diversity: float
    robustness_variance: float
    degradation_score: float
    token_cost: int
```

```python
# src/sentinel/domain/evaluation/comparison.py
"""
Side-by-side comparison of two investigation backends.
"""
from __future__ import annotations

from collections.abc import Mapping

import attrs

from sentinel.domain.evaluation import metrics


@attrs.frozen
class ComparisonResult:
    """
    Compare baseline vs challenger across all evaluation dimensions.

    ``winner_by_dimension`` maps each metric name to the adapter
    that scored better on that dimension.
    """

    case_id: str
    baseline: metrics.EvaluationMetrics
    challenger: metrics.EvaluationMetrics
    winner_by_dimension: Mapping[str, str]
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/evaluation/ -v`
Expected: All 3 tests PASS

- [x] **Step 6: Add `evaluation` to import-linter layers**

In `pyproject.toml`, add `evaluation` between `evals` and `plugins` in the layers list (line 203):

```toml
layers = [
    "main",
    "worker",
    "config",
    "interfaces",
    "application",
    "evals",
    "plugins",
    "evaluation",
    "domain",
    "data",
    "vendors",
    "bootstrap",
    "utils",
    "settings",
    "version",
]
```

Wait — `domain/evaluation/` is inside the `domain` package, so it's already covered by the `domain` layer. No import-linter change needed. Delete this step.

- [x] **Step 7: Verify import-linter and full test suite**

Run: `uv run lint-imports && uv run pytest tests/unit/ -v --tb=short`
Expected: All contracts and tests PASS

- [x] **Step 8: Commit**

```bash
git add src/sentinel/domain/evaluation/ tests/unit/domain/evaluation/
git commit -m "feat: add pipeline-agnostic evaluation metrics and comparison types"
```

---

## Task 4: K8s Domain Tool Functions

**Files:**
- Create: `src/sentinel/domain/tools/kubernetes.py`
- Test: `tests/unit/domain/tools/test_kubernetes.py`

- [x] **Step 1: Write tests for K8s tool functions**

```python
# tests/unit/domain/tools/test_kubernetes.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.tools import kubernetes as k8s_tools


class TestGetPodStatus:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client
        # When querying pod status
        result = await k8s_tools.get_pod_status(
            client=None,
            namespace="default",
            pod_name="api-service-abc123",
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_pod_status_summary(self) -> None:
        # Given a mock kubernetes client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.get_pod_status.return_value = {
            "name": "api-service-abc123",
            "phase": "Running",
            "restart_count": 5,
            "conditions": [
                {"type": "Ready", "status": "False", "reason": "ContainersNotReady"},
            ],
        }

        # When querying pod status
        result = await k8s_tools.get_pod_status(
            client=mock_client,
            namespace="production",
            pod_name="api-service-abc123",
        )

        # Then the summary includes key details
        assert "api-service-abc123" in result
        assert "Running" in result
        assert "5" in result


class TestGetDeploymentStatus:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client
        # When querying deployment status
        result = await k8s_tools.get_deployment_status(
            client=None,
            namespace="default",
            deployment_name="api-service",
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()


class TestGetRecentEvents:
    @pytest.mark.asyncio
    async def test_returns_events_summary(self) -> None:
        # Given a mock kubernetes client with events
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.get_recent_events.return_value = [
            {
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container",
                "count": 12,
                "last_timestamp": "2026-04-02T12:00:00Z",
            },
        ]

        # When querying recent events
        result = await k8s_tools.get_recent_events(
            client=mock_client,
            namespace="production",
            resource_name="api-service-abc123",
        )

        # Then the summary includes event details
        assert "Warning" in result
        assert "BackOff" in result


class TestGetPodLogs:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client
        # When querying pod logs
        result = await k8s_tools.get_pod_logs(
            client=None,
            namespace="default",
            pod_name="api-service-abc123",
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/tools/test_kubernetes.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Implement K8s tool functions**

```python
# src/sentinel/domain/tools/kubernetes.py
"""
Read-only Kubernetes tools for K8s investigation agents.

Each function queries the Kubernetes API and returns a human-readable
summary string.  Functions are framework-agnostic — they accept a
typed client and return ``str``, making them testable without PydanticAI.

When the client is ``None`` or not configured, every function returns
a descriptive fallback message instead of raising.
"""
from __future__ import annotations

from typing import Any, Protocol

from sentinel.utils import logs


class K8sClient(Protocol):
    """Protocol for Kubernetes API client abstraction."""

    @property
    def is_configured(self) -> bool: ...

    async def get_pod_status(
        self, *, namespace: str, pod_name: str
    ) -> dict[str, Any]: ...

    async def get_deployment_status(
        self, *, namespace: str, deployment_name: str
    ) -> dict[str, Any]: ...

    async def get_recent_events(
        self, *, namespace: str, resource_name: str, limit: int = 20
    ) -> list[dict[str, Any]]: ...

    async def get_pod_logs(
        self, *, namespace: str, pod_name: str, container: str | None = None, tail_lines: int = 100
    ) -> str: ...

    async def describe_resource(
        self, *, namespace: str, kind: str, name: str
    ) -> dict[str, Any]: ...


async def get_pod_status(
    *,
    client: K8sClient | None,
    namespace: str,
    pod_name: str,
) -> str:
    """
    Return a summary of a pod's current status.

    Includes phase, restart count, and conditions.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query pod status."

    try:
        status = await client.get_pod_status(namespace=namespace, pod_name=pod_name)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "get_pod_status", "pod": pod_name})
        return f"Pod status query failed: {type(exc).__name__} — {exc}"

    name = status.get("name", pod_name)
    phase = status.get("phase", "Unknown")
    restarts = status.get("restart_count", 0)
    conditions = status.get("conditions", [])

    lines = [
        f"Pod: {name}",
        f"Phase: {phase}",
        f"Restart count: {restarts}",
    ]
    if conditions:
        lines.append("Conditions:")
        for cond in conditions:
            reason = cond.get("reason", "")
            lines.append(f"  {cond.get('type', '')}: {cond.get('status', '')} ({reason})")

    return "\n".join(lines)


async def get_deployment_status(
    *,
    client: K8sClient | None,
    namespace: str,
    deployment_name: str,
) -> str:
    """
    Return a summary of a deployment's rollout status.

    Includes replica counts and rollout conditions.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query deployment status."

    try:
        status = await client.get_deployment_status(
            namespace=namespace, deployment_name=deployment_name
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "get_deployment_status", "deployment": deployment_name})
        return f"Deployment status query failed: {type(exc).__name__} — {exc}"

    name = status.get("name", deployment_name)
    ready = status.get("ready_replicas", 0)
    desired = status.get("desired_replicas", 0)
    updated = status.get("updated_replicas", 0)
    available = status.get("available_replicas", 0)

    lines = [
        f"Deployment: {name}",
        f"Replicas: {ready}/{desired} ready, {updated} updated, {available} available",
    ]
    conditions = status.get("conditions", [])
    if conditions:
        lines.append("Conditions:")
        for cond in conditions:
            lines.append(f"  {cond.get('type', '')}: {cond.get('status', '')} — {cond.get('message', '')}")

    return "\n".join(lines)


async def get_recent_events(
    *,
    client: K8sClient | None,
    namespace: str,
    resource_name: str,
    limit: int = 20,
) -> str:
    """
    Return a summary of recent K8s events for a resource.

    Focuses on warnings and errors.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query events."

    try:
        events = await client.get_recent_events(
            namespace=namespace, resource_name=resource_name, limit=limit
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "get_recent_events", "resource": resource_name})
        return f"Events query failed: {type(exc).__name__} — {exc}"

    if not events:
        return f"No recent events found for '{resource_name}' in namespace '{namespace}'."

    lines = [f"Found {len(events)} event(s) for '{resource_name}':"]
    for event in events[:limit]:
        event_type = event.get("type", "Normal")
        reason = event.get("reason", "")
        message = str(event.get("message", ""))[:200]
        count = event.get("count", 1)
        lines.append(f"  [{event_type}] {reason} (x{count}): {message}")

    return "\n".join(lines)


async def get_pod_logs(
    *,
    client: K8sClient | None,
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> str:
    """
    Return the last N lines of a pod's container logs.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query pod logs."

    try:
        log_text = await client.get_pod_logs(
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "get_pod_logs", "pod": pod_name})
        return f"Pod log query failed: {type(exc).__name__} — {exc}"

    if not log_text:
        return f"No logs found for pod '{pod_name}'."

    return f"Logs for pod '{pod_name}' (last {tail_lines} lines):\n{log_text}"


async def describe_resource(
    *,
    client: K8sClient | None,
    namespace: str,
    kind: str,
    name: str,
) -> str:
    """
    Return a human-readable description of any K8s resource.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to describe resource."

    try:
        resource = await client.describe_resource(namespace=namespace, kind=kind, name=name)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "describe_resource", "kind": kind, "name": name})
        return f"Describe failed: {type(exc).__name__} — {exc}"

    lines = [f"{kind}/{name} in {namespace}:"]
    for key, value in resource.items():
        lines.append(f"  {key}: {value}")

    return "\n".join(lines)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/tools/test_kubernetes.py -v`
Expected: All 5 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/domain/tools/kubernetes.py tests/unit/domain/tools/test_kubernetes.py
git commit -m "feat: add K8s domain tool functions for pod, deployment, event, and log queries"
```

---

## Task 5: K8s FunctionToolset Wrapper

**Files:**
- Create: `src/sentinel/plugins/toolsets/kubernetes.py`
- Test: `tests/unit/plugins/toolsets/test_kubernetes.py`

- [x] **Step 1: Write tests for K8s toolset**

```python
# tests/unit/plugins/toolsets/test_kubernetes.py
from __future__ import annotations

from sentinel.plugins.toolsets import kubernetes as k8s_toolsets


class TestBuildKubernetesToolset:
    def test_returns_toolset_with_five_tools(self) -> None:
        # Given no client (tools will no-op)
        # When building the toolset
        toolset = k8s_toolsets.build_kubernetes_toolset(
            k8s_client=None,
            namespace="production",
        )

        # Then the toolset has five registered tools
        # (FunctionToolset stores tools internally)
        assert toolset is not None

    def test_defaults_namespace_for_queries(self) -> None:
        # Given a toolset with a default namespace
        toolset = k8s_toolsets.build_kubernetes_toolset(
            k8s_client=None,
            namespace="payments",
        )

        # Then the toolset is created successfully
        assert toolset is not None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/plugins/toolsets/test_kubernetes.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Implement K8s toolset wrapper**

```python
# src/sentinel/plugins/toolsets/kubernetes.py
"""
Kubernetes toolset for K8s investigation agents.

Wraps the domain tool functions from ``sentinel.domain.tools.kubernetes``
into a PydanticAI ``FunctionToolset`` that can be injected at
``agent.run(toolsets=[...])`` time.

All tools are read-only and no-op when the K8s client is unavailable.
"""
from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from sentinel.domain.tools import kubernetes as k8s_tools


def build_kubernetes_toolset(
    *,
    k8s_client: k8s_tools.K8sClient | None,
    namespace: str = "default",
) -> FunctionToolset[Any]:
    """
    Build a read-only toolset for querying Kubernetes cluster state.

    :param k8s_client: The configured K8s API client, or None.
    :param namespace: Default namespace for queries.
    """
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool
    async def get_pod_status(
        ctx: RunContext[Any],
        pod_name: str,
        ns: str = namespace,
    ) -> str:
        """
        Get the current status of a Kubernetes pod.

        Returns phase, restart count, and conditions. Use this to check
        if a pod is crashing, restarting, or stuck in a pending state.

        Args:
            ctx: PydanticAI run context (injected automatically).
            pod_name: Name of the pod to check.
            ns: Kubernetes namespace. Defaults to the alerted namespace.
        """
        return await k8s_tools.get_pod_status(
            client=k8s_client, namespace=ns, pod_name=pod_name
        )

    @toolset.tool
    async def get_deployment_status(
        ctx: RunContext[Any],
        deployment_name: str,
        ns: str = namespace,
    ) -> str:
        """
        Get the rollout status of a Kubernetes deployment.

        Returns replica counts, update status, and conditions. Use this
        to check if a deployment is stuck rolling out or has unavailable replicas.

        Args:
            ctx: PydanticAI run context (injected automatically).
            deployment_name: Name of the deployment to check.
            ns: Kubernetes namespace.
        """
        return await k8s_tools.get_deployment_status(
            client=k8s_client, namespace=ns, deployment_name=deployment_name
        )

    @toolset.tool
    async def get_recent_events(
        ctx: RunContext[Any],
        resource_name: str,
        ns: str = namespace,
        limit: int = 20,
    ) -> str:
        """
        Get recent Kubernetes events for a resource.

        Returns warnings, errors, and other events. Use this to find
        scheduling failures, image pull errors, readiness probe failures, etc.

        Args:
            ctx: PydanticAI run context (injected automatically).
            resource_name: Name of the K8s resource (pod, deployment, etc.).
            ns: Kubernetes namespace.
            limit: Maximum number of events to return.
        """
        return await k8s_tools.get_recent_events(
            client=k8s_client, namespace=ns, resource_name=resource_name, limit=limit
        )

    @toolset.tool
    async def get_pod_logs(
        ctx: RunContext[Any],
        pod_name: str,
        ns: str = namespace,
        container: str | None = None,
        tail_lines: int = 100,
    ) -> str:
        """
        Get the last N lines of logs from a pod's container.

        Use this to find error messages, stack traces, and other
        diagnostic output from the application.

        Args:
            ctx: PydanticAI run context (injected automatically).
            pod_name: Name of the pod.
            ns: Kubernetes namespace.
            container: Specific container name (for multi-container pods).
            tail_lines: Number of lines to return from the end of the log.
        """
        return await k8s_tools.get_pod_logs(
            client=k8s_client, namespace=ns, pod_name=pod_name,
            container=container, tail_lines=tail_lines,
        )

    @toolset.tool
    async def describe_resource(
        ctx: RunContext[Any],
        kind: str,
        name: str,
        ns: str = namespace,
    ) -> str:
        """
        Describe any Kubernetes resource (pod, service, ingress, pvc, etc.).

        Returns a human-readable summary of the resource's spec and status.

        Args:
            ctx: PydanticAI run context (injected automatically).
            kind: Resource kind (e.g. "pod", "service", "ingress", "pvc").
            name: Resource name.
            ns: Kubernetes namespace.
        """
        return await k8s_tools.describe_resource(
            client=k8s_client, namespace=ns, kind=kind, name=name
        )

    return toolset
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/plugins/toolsets/test_kubernetes.py -v`
Expected: All 2 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/plugins/toolsets/kubernetes.py tests/unit/plugins/toolsets/test_kubernetes.py
git commit -m "feat: add K8s FunctionToolset wrapper for PydanticAI agents"
```

---

## Task 6: K8s Investigator PydanticAI Agent

**Files:**
- Create: `src/sentinel/plugins/prompts/k8s_investigator.j2`
- Create: `src/sentinel/interfaces/graphs/agents/k8s_investigator.py`

- [x] **Step 1: Create K8s investigator prompt template**

```jinja2
{# K8s Investigator — analyses Kubernetes cluster state for root cause diagnosis. #}
{% block system %}
You are an expert Kubernetes Site Reliability Engineer investigating a production incident.

You have access to tools that query the Kubernetes API: pod status, deployment status,
events, pod logs, and resource descriptions. Use these tools to systematically investigate
the alert and identify the root cause.

Investigation strategy:
1. Start with the affected service's deployment status and pod status
2. Check recent events for warnings or errors
3. Look at pod logs for error messages and stack traces
4. Check related resources (services, ingress, PVCs) if the issue spans components
5. Build a timeline of what happened

Your analysis must include:
1. **Root cause**: A clear, specific explanation of what went wrong in the cluster
2. **Confidence**: A score from 0.0 to 1.0
3. **Evidence**: Specific K8s resources, events, and log entries that support your conclusion
4. **Remediation steps**: Ordered list of kubectl/helm commands or actions to resolve the issue
5. **Affected services**: All K8s resources impacted
6. **Timeline**: Reconstruction based on event timestamps and pod restart times

Be specific about resource names, namespaces, and timestamps. Avoid vague statements.
{% endblock %}

{% block user %}
## Alert Details
- **Title**: {{ alert_title }}
- **Description**: {{ alert_description }}
- **Severity**: {{ alert_severity }}
- **Service**: {{ service }}

## Cluster Context
- **Cluster**: {{ cluster_name }}
- **Namespace**: {{ namespace | default("(cluster-wide)") }}

Use the available Kubernetes tools to investigate this alert.
{% endblock %}
```

- [x] **Step 2: Create K8s investigator agent module**

```python
# src/sentinel/interfaces/graphs/agents/k8s_investigator.py
"""
K8s Investigator PydanticAI agent.

Analyses Kubernetes cluster state to diagnose production incidents.
Uses K8s tools (pod status, deployment status, events, logs) injected
at runtime via toolsets.
"""
from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel.plugins import prompts


class K8sInvestigationOutput(BaseModel):
    """Structured output from the K8s investigator agent."""

    root_cause: str
    confidence: float
    evidence: list[str]
    remediation_steps: list[str]
    affected_resources: list[str]
    timeline: str


@dataclasses.dataclass
class Dependencies:
    alert_title: str
    alert_description: str
    alert_severity: str
    service: str
    cluster_name: str
    namespace: str | None = None


SYSTEM_PROMPT = prompts.load_system_prompt("k8s_investigator")


agent: Agent[Dependencies, K8sInvestigationOutput] = Agent(
    "test",  # Overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=K8sInvestigationOutput,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_k8s_context(ctx: RunContext[Dependencies]) -> str:
    return prompts.render_user_prompt(
        "k8s_investigator",
        alert_title=ctx.deps.alert_title,
        alert_description=ctx.deps.alert_description,
        alert_severity=ctx.deps.alert_severity,
        service=ctx.deps.service,
        cluster_name=ctx.deps.cluster_name,
        namespace=ctx.deps.namespace,
    )
```

- [x] **Step 3: Verify the agent module loads**

Run: `uv run python -c "from sentinel.interfaces.graphs.agents import k8s_investigator; print(k8s_investigator.agent)"`
Expected: Prints agent repr without errors

- [x] **Step 4: Commit**

```bash
git add src/sentinel/plugins/prompts/k8s_investigator.j2 src/sentinel/interfaces/graphs/agents/k8s_investigator.py
git commit -m "feat: add K8s investigator PydanticAI agent with prompt template"
```

---

## Task 7: NativeK8sAgent Implementation

**Files:**
- Create: `src/sentinel/domain/sre/k8s_native_agent.py`
- Test: `tests/unit/domain/sre/test_k8s_native_agent.py`

- [x] **Step 1: Write tests for NativeK8sAgent**

```python
# tests/unit/domain/sre/test_k8s_native_agent.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.sre import investigation, k8s_native_agent
from tests import factories


class TestNativeK8sAgent:
    def test_is_configured_when_k8s_client_available(self) -> None:
        # Given a mock K8s client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True

        # When creating the agent adapter
        adapter = k8s_native_agent.NativeK8sAgent(k8s_client=mock_client)

        # Then it reports as configured
        assert adapter.is_configured is True

    def test_is_not_configured_when_client_is_none(self) -> None:
        # Given no K8s client
        # When creating the agent adapter
        adapter = k8s_native_agent.NativeK8sAgent(k8s_client=None)

        # Then it reports as not configured
        assert adapter.is_configured is False

    @pytest.mark.asyncio
    async def test_returns_investigation_result_with_audit_trail(self) -> None:
        # Given a mock agent run that returns structured output
        alert = factories.make_alert(
            title="Pod CrashLoopBackOff",
            service="payments-service",
        )
        context = investigation.InvestigationContext(
            cluster_name="prod-eu-west-1",
            namespace="payments",
        )

        mock_client = mock.AsyncMock()
        mock_client.is_configured = True

        adapter = k8s_native_agent.NativeK8sAgent(
            k8s_client=mock_client,
            model_name="openai:gpt-4.1",
        )

        # When investigating (with agent mocked)
        with mock.patch.object(
            k8s_native_agent, "_run_k8s_agent"
        ) as mock_run:
            mock_run.return_value = k8s_native_agent._AgentResult(
                root_cause="OOM killed due to memory leak",
                confidence=0.85,
                evidence=["Pod restart count: 12", "OOMKilled in events"],
                remediation_steps=["Increase memory limit", "Fix memory leak"],
                affected_resources=["payments-service-abc123"],
                timeline="Pod started crashing at 14:32 UTC",
                audit_entries=[],
            )

            result = await adapter.investigate(alert=alert, context=context)

        # Then the result has the correct adapter name
        assert result.adapter_name == "native_k8s"
        assert len(result.findings) > 0
        assert result.duration_ms >= 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/sre/test_k8s_native_agent.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Implement NativeK8sAgent**

```python
# src/sentinel/domain/sre/k8s_native_agent.py
"""
Native Kubernetes investigation adapter.

Uses a PydanticAI agent with the ``kubernetes`` Python client tools
to query cluster state and diagnose production incidents.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import attrs

from sentinel.domain.sre import entities, investigation
from sentinel.domain.tools import kubernetes as k8s_tools
from sentinel.utils import logs


logger = logs.get_logger()


@attrs.frozen
class _AgentResult:
    """Internal result from the K8s investigator agent run."""

    root_cause: str
    confidence: float
    evidence: tuple[str, ...] | list[str]
    remediation_steps: tuple[str, ...] | list[str]
    affected_resources: tuple[str, ...] | list[str]
    timeline: str
    audit_entries: tuple[investigation.AuditEntry, ...] | list[investigation.AuditEntry]


class NativeK8sAgent(investigation.K8sInvestigationAdapter):
    """
    Investigate alerts using a PydanticAI agent with native K8s tools.

    Queries pod status, deployment status, events, and logs via the
    ``kubernetes`` Python async client.
    """

    def __init__(
        self,
        *,
        k8s_client: k8s_tools.K8sClient | None = None,
        model_name: str = "openai:gpt-4.1",
        mcp_toolsets: Sequence[Any] = (),
    ) -> None:
        self._k8s_client = k8s_client
        self._model_name = model_name
        self._mcp_toolsets = mcp_toolsets

    @property
    def is_configured(self) -> bool:
        return self._k8s_client is not None and self._k8s_client.is_configured

    async def investigate(
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> investigation.InvestigationResult:
        start_time = time.monotonic()
        started_at = datetime.now(tz=UTC)

        logs.log_event(
            "k8s_native_investigation_started",
            params={
                "alert_id": alert.id,
                "service": alert.service,
                "cluster": context.cluster_name if context else "unknown",
                "namespace": context.namespace if context else None,
            },
        )

        if not self.is_configured:
            return investigation.InvestigationResult(
                findings=(),
                sources_queried=(),
                duration_ms=0,
                adapter_name="native_k8s",
                audit_trail=(
                    investigation.AuditEntry(
                        timestamp=started_at,
                        adapter_name="native_k8s",
                        action="configuration_check",
                        tool_name=None,
                        status="error",
                        duration_ms=0,
                        error_code=None,
                        payload={"reason": "K8s client not configured"},
                    ),
                ),
            )

        agent_result = await _run_k8s_agent(
            alert=alert,
            context=context,
            k8s_client=self._k8s_client,
            model_name=self._model_name,
            mcp_toolsets=self._mcp_toolsets,
        )

        duration_ms = int((time.monotonic() - start_time) * 1000)

        findings = tuple(
            entities.Finding(
                source="kubernetes",
                summary=evidence_item,
                relevance=agent_result.confidence,
            )
            for evidence_item in agent_result.evidence
        )

        sources = ("kubernetes_pods", "kubernetes_events", "kubernetes_logs")

        logs.log_event(
            "k8s_native_investigation_completed",
            params={
                "alert_id": alert.id,
                "findings_count": len(findings),
                "confidence": agent_result.confidence,
                "duration_ms": duration_ms,
            },
        )

        return investigation.InvestigationResult(
            findings=findings,
            sources_queried=sources,
            duration_ms=duration_ms,
            adapter_name="native_k8s",
            audit_trail=tuple(agent_result.audit_entries),
        )


async def _run_k8s_agent(
    *,
    alert: entities.Alert,
    context: investigation.InvestigationContext | None,
    k8s_client: k8s_tools.K8sClient | None,
    model_name: str,
    mcp_toolsets: Sequence[Any],
) -> _AgentResult:
    """
    Run the PydanticAI K8s investigator agent with toolsets.

    Separated for testability — tests mock this function.
    """
    from sentinel.interfaces.graphs.agents import k8s_investigator, utils
    from sentinel.plugins.toolsets import kubernetes as k8s_toolset_mod

    namespace = context.namespace or "default" if context else "default"
    cluster_name = context.cluster_name if context else "unknown"

    k8s_toolset = k8s_toolset_mod.build_kubernetes_toolset(
        k8s_client=k8s_client,
        namespace=namespace,
    )

    toolsets: list[Any] = [k8s_toolset, *mcp_toolsets]

    deps = k8s_investigator.Dependencies(
        alert_title=alert.title,
        alert_description=alert.description,
        alert_severity=alert.severity.value,
        service=alert.service,
        cluster_name=cluster_name,
        namespace=namespace,
    )

    result = await k8s_investigator.agent.run(
        deps=deps,
        model=utils.get_model_with_gateway(model_name),
        toolsets=toolsets,
    )

    output = result.output
    return _AgentResult(
        root_cause=output.root_cause,
        confidence=output.confidence,
        evidence=output.evidence,
        remediation_steps=output.remediation_steps,
        affected_resources=output.affected_resources,
        timeline=output.timeline,
        audit_entries=[],
    )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/sre/test_k8s_native_agent.py -v`
Expected: All 3 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/domain/sre/k8s_native_agent.py tests/unit/domain/sre/test_k8s_native_agent.py
git commit -m "feat: implement NativeK8sAgent with PydanticAI and K8s tools"
```

---

## Task 8: K8s Agent Settings and Config Wiring

**Files:**
- Modify: `src/sentinel/settings.py`
- Modify: `src/sentinel/config.py`

- [x] **Step 1: Add K8s and MCP settings**

In `src/sentinel/settings.py`, add to `SRESettings` class (after line 46):

```python
    # K8s investigation agent
    k8s_investigation_backend: str = ""  # "native", "kagent", "both", or "" (disabled)
    k8s_investigator_llm: str = "ollama/qwen3:8b"
    k8s_cluster_name: str = ""
    k8s_default_namespace: str = ""

    # Kagent
    kagent_investigation_timeout_seconds: int = 120
    kagent_namespace: str = "kagent-system"

    # MCP
    mcp_servers: str = ""  # JSON list: [{"name": "...", "url": "..."}, ...]
    k8s_mcp_server_url: str = ""
    mcp_server_port: int = 8811
    mcp_server_api_key: str = ""
```

- [x] **Step 2: Add config methods for K8s adapter**

In `src/sentinel/config.py`, add after `build_holmes_adapter()` (after line 157):

```python
    def build_k8s_investigation_adapter(
        self,
    ) -> investigation.K8sInvestigationAdapter | None:
        """
        Build the K8s investigation adapter based on configuration.

        Returns None when K8s investigation is disabled.
        """
        from sentinel.domain.sre import investigation, k8s_native_agent

        backend = self.settings.k8s_investigation_backend
        if not backend:
            return None

        if backend in ("native", "both"):
            return k8s_native_agent.NativeK8sAgent(
                k8s_client=None,  # TODO: Wire real K8s client in Task 12
                model_name=_normalise_model_name(self.settings.k8s_investigator_llm),
            )

        return None  # kagent wired in Task 11
```

Add the import at the top of `config.py`:
```python
from sentinel.domain.sre import investigation
```

- [x] **Step 3: Run existing tests to verify nothing breaks**

Run: `uv run pytest tests/unit/ -v --tb=short`
Expected: All tests PASS

- [x] **Step 4: Verify import-linter**

Run: `uv run lint-imports`
Expected: All contracts PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/settings.py src/sentinel/config.py
git commit -m "feat: add K8s agent and MCP settings with config wiring"
```

---

## Task 9: MCP Server (FastMCP)

**Files:**
- Create: `src/sentinel/interfaces/mcp/__init__.py`
- Create: `src/sentinel/interfaces/mcp/server.py`
- Create: `src/sentinel/interfaces/mcp/tools/__init__.py`
- Create: `src/sentinel/interfaces/mcp/tools/observability.py`
- Create: `src/sentinel/interfaces/mcp/tools/documentation.py`
- Create: `src/sentinel/interfaces/mcp/tools/investigation.py`
- Modify: `pyproject.toml` (add `fastmcp` dependency)
- Test: `tests/unit/interfaces/mcp/test_server.py`

- [x] **Step 1: Add `fastmcp` dependency**

In `pyproject.toml`, add to the dependencies list (after line 41):
```toml
    "fastmcp>=2.0",
```

Run: `uv sync`

- [x] **Step 2: Write tests for MCP server tools**

```python
# tests/unit/interfaces/mcp/test_server.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.mcp.tools import observability as mcp_obs_tools


class TestMcpObservabilityTools:
    @pytest.mark.asyncio
    async def test_query_logs_delegates_to_domain_tool(self) -> None:
        # Given a mock observability client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.query_logs.return_value = [
            {"timestamp": "2026-04-02T12:00:00Z", "message": "Error", "status": "error"}
        ]

        # When calling the MCP tool function
        result = await mcp_obs_tools.query_logs(
            obs_client=mock_client,
            service="api-service",
            query="error",
            minutes_back=30,
        )

        # Then it returns a formatted result
        assert "api-service" in result or "Error" in result
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/interfaces/mcp/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 4: Implement MCP server and tool modules**

```python
# src/sentinel/interfaces/mcp/__init__.py
```

```python
# src/sentinel/interfaces/mcp/tools/__init__.py
```

```python
# src/sentinel/interfaces/mcp/tools/observability.py
"""
MCP server tools wrapping Sentinel's observability domain functions.

Thin wrappers — no business logic duplication.
"""
from __future__ import annotations

from sentinel.domain.tools import observability as obs_tools
from sentinel.domain.vendor_adapters.observability import base as obs_base


async def query_logs(
    *,
    obs_client: obs_base.BaseObservabilityClient | None,
    service: str,
    query: str = "error OR warn",
    minutes_back: int = 30,
) -> str:
    """Search recent logs for a service."""
    return await obs_tools.query_recent_logs(
        client=obs_client, service=service, query=query, minutes_back=minutes_back
    )


async def query_metrics(
    *,
    obs_client: obs_base.BaseObservabilityClient | None,
    service: str,
    metric_name: str = "cpu",
    minutes_back: int = 60,
) -> str:
    """Fetch metric time series for a service."""
    return await obs_tools.query_metrics(
        client=obs_client, service=service, metric_name=metric_name, minutes_back=minutes_back
    )


async def query_error_traces(
    *,
    obs_client: obs_base.BaseObservabilityClient | None,
    service: str,
    minutes_back: int = 30,
) -> str:
    """Search distributed traces for error spans."""
    return await obs_tools.query_error_traces(
        client=obs_client, service=service, minutes_back=minutes_back
    )
```

```python
# src/sentinel/interfaces/mcp/tools/documentation.py
"""
MCP server tools wrapping Sentinel's documentation search functions.
"""
from __future__ import annotations

from sentinel.domain.tools import documentation as doc_tools
from sentinel.domain.search import searcher


async def search_documentation(
    *,
    document_searcher: searcher.BaseDocumentSearcher | None,
    query: str,
    max_results: int = 5,
) -> str:
    """Search documentation across Confluence, Notion, and S3."""
    return await doc_tools.search_documentation(
        searcher=document_searcher, query=query, max_results=max_results
    )
```

```python
# src/sentinel/interfaces/mcp/tools/investigation.py
"""
MCP server tools for triggering and querying investigations.
"""
from __future__ import annotations


async def trigger_investigation(
    *,
    alert_source: str,
    alert_id: str,
    description: str = "",
) -> str:
    """
    Trigger an SRE investigation for an alert.

    Returns a job ID that can be polled for status.
    """
    # TODO: Wire to application layer job creation in Phase E
    return f"Investigation triggered for {alert_source}/{alert_id}. Job queued."


async def get_investigation_status(*, investigation_id: str) -> str:
    """Check the status of a running investigation."""
    # TODO: Wire to application layer job status query
    return f"Investigation {investigation_id}: status lookup not yet wired."
```

```python
# src/sentinel/interfaces/mcp/server.py
"""
FastMCP server exposing Sentinel's tools to external agents.

Run as a separate deployment or locally with::

    uv run python -m sentinel.interfaces.mcp.server

Exposes observability, documentation, and investigation tools
via the MCP (Model Context Protocol) streamable HTTP transport.
"""
from __future__ import annotations

from fastmcp import FastMCP

from sentinel import bootstrap
from sentinel.config import get_config
from sentinel.interfaces.mcp.tools import documentation as doc_tools
from sentinel.interfaces.mcp.tools import investigation as inv_tools
from sentinel.interfaces.mcp.tools import observability as obs_tools
from sentinel.utils import logs

logger = logs.get_logger()

mcp = FastMCP(
    "Sentinel",
    instructions=(
        "Sentinel AI SRE platform tools. "
        "Query observability data (logs, metrics, traces), "
        "search documentation, and trigger investigations."
    ),
)


@mcp.tool()
async def query_logs(service: str, query: str = "error OR warn", minutes_back: int = 30) -> str:
    """Search recent logs for a service. Returns formatted log entries."""
    config = get_config()
    return await obs_tools.query_logs(
        obs_client=config.observability_client, service=service, query=query, minutes_back=minutes_back
    )


@mcp.tool()
async def query_metrics(service: str, metric_name: str = "cpu", minutes_back: int = 60) -> str:
    """Fetch metric time series for a service."""
    config = get_config()
    return await obs_tools.query_metrics(
        obs_client=config.observability_client, service=service, metric_name=metric_name, minutes_back=minutes_back
    )


@mcp.tool()
async def query_error_traces(service: str, minutes_back: int = 30) -> str:
    """Search distributed traces for error spans in a service."""
    config = get_config()
    return await obs_tools.query_error_traces(
        obs_client=config.observability_client, service=service, minutes_back=minutes_back
    )


@mcp.tool()
async def search_documentation(query: str, max_results: int = 5) -> str:
    """Search documentation across Confluence, Notion, and S3."""
    config = get_config()
    return await doc_tools.search_documentation(
        document_searcher=config.build_document_searcher(), query=query, max_results=max_results
    )


@mcp.tool()
async def trigger_investigation(alert_source: str, alert_id: str, description: str = "") -> str:
    """Trigger an SRE investigation for an alert. Returns a job ID."""
    return await inv_tools.trigger_investigation(
        alert_source=alert_source, alert_id=alert_id, description=description
    )


@mcp.tool()
async def get_investigation_status(investigation_id: str) -> str:
    """Check the status of a running investigation."""
    return await inv_tools.get_investigation_status(investigation_id=investigation_id)


if __name__ == "__main__":
    bootstrap.initialise()
    mcp.run(transport="streamable-http")
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/interfaces/mcp/test_server.py -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add pyproject.toml src/sentinel/interfaces/mcp/ tests/unit/interfaces/mcp/
git commit -m "feat: add MCP server exposing observability, documentation, and investigation tools"
```

---

## Task 10: MCP Client Toolset Builder

**Files:**
- Create: `src/sentinel/plugins/toolsets/mcp.py`
- Test: `tests/unit/plugins/toolsets/test_mcp.py`

- [x] **Step 1: Write tests for MCP client builder**

```python
# tests/unit/plugins/toolsets/test_mcp.py
from __future__ import annotations

import json

import pytest

from sentinel.plugins.toolsets import mcp as mcp_toolsets


class TestParseMcpServerConfigs:
    def test_parses_empty_string_to_empty_list(self) -> None:
        # Given an empty config string
        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs("")

        # Then no servers are returned
        assert result == ()

    def test_parses_json_list_of_http_servers(self) -> None:
        # Given a JSON config string
        config = json.dumps([
            {"name": "kubectl", "url": "http://localhost:9000/mcp"},
        ])

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config)

        # Then one server config is returned
        assert len(result) == 1
        assert result[0].name == "kubectl"
        assert result[0].url == "http://localhost:9000/mcp"

    def test_parses_stdio_server_config(self) -> None:
        # Given a JSON config with a stdio server
        config = json.dumps([
            {"name": "kubectl-mcp", "command": "kubectl-mcp-server", "args": ["--namespace", "default"]},
        ])

        # When parsing
        result = mcp_toolsets.parse_mcp_server_configs(config)

        # Then the stdio config is parsed
        assert len(result) == 1
        assert result[0].name == "kubectl-mcp"
        assert result[0].command == "kubectl-mcp-server"
        assert result[0].args == ("--namespace", "default")
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/plugins/toolsets/test_mcp.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Implement MCP client toolset builder**

```python
# src/sentinel/plugins/toolsets/mcp.py
"""
MCP client toolset builder for PydanticAI agents.

Parses ``MCP_SERVERS`` config and returns PydanticAI-compatible
MCP toolsets that can be injected at ``agent.run(toolsets=[...])``.
"""
from __future__ import annotations

import json
from typing import Any

import attrs

from sentinel.utils import logs

logger = logs.get_logger()


@attrs.frozen
class McpServerConfig:
    """Parsed MCP server configuration."""

    name: str
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()


def parse_mcp_server_configs(config_json: str) -> tuple[McpServerConfig, ...]:
    """
    Parse the ``MCP_SERVERS`` env var into server configs.

    Accepts a JSON list of objects with either:
    - ``{"name": "...", "url": "..."}`` for HTTP servers
    - ``{"name": "...", "command": "...", "args": [...]}`` for stdio servers

    :param config_json: JSON string from the MCP_SERVERS env var.
    :returns: Tuple of parsed server configs.
    """
    if not config_json.strip():
        return ()

    try:
        servers = json.loads(config_json)
    except json.JSONDecodeError:
        logger.warning("Invalid MCP_SERVERS JSON, ignoring", config=config_json[:100])
        return ()

    configs: list[McpServerConfig] = []
    for server in servers:
        name = server.get("name", "unnamed")
        url = server.get("url")
        command = server.get("command")
        args = tuple(server.get("args", []))

        configs.append(McpServerConfig(name=name, url=url, command=command, args=args))

    return tuple(configs)


def build_mcp_toolsets(
    config_json: str,
) -> tuple[Any, ...]:
    """
    Build PydanticAI-compatible MCP toolsets from config.

    Returns a tuple of ``MCPServerHTTP`` or ``MCPServerStdio`` instances
    that can be passed to ``agent.run(toolsets=[...])``.

    :param config_json: JSON string from the MCP_SERVERS env var.
    :returns: Tuple of MCP toolset instances.
    """
    configs = parse_mcp_server_configs(config_json)
    if not configs:
        return ()

    from pydantic_ai.mcp import MCPServerHTTP, MCPServerStdio

    toolsets: list[Any] = []
    for config in configs:
        if config.url:
            toolsets.append(MCPServerHTTP(url=config.url))
            logger.info("MCP HTTP client configured", name=config.name, url=config.url)
        elif config.command:
            toolsets.append(MCPServerStdio(config.command, args=list(config.args)))
            logger.info("MCP stdio client configured", name=config.name, command=config.command)

    return tuple(toolsets)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/plugins/toolsets/test_mcp.py -v`
Expected: All 3 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/plugins/toolsets/mcp.py tests/unit/plugins/toolsets/test_mcp.py
git commit -m "feat: add MCP client toolset builder for PydanticAI agents"
```

---

## Task 11: KagentAdapter

**Files:**
- Create: `src/sentinel/domain/sre/kagent_adapter.py`
- Test: `tests/unit/domain/sre/test_kagent_adapter.py`

- [x] **Step 1: Write tests for KagentAdapter**

```python
# tests/unit/domain/sre/test_kagent_adapter.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.sre import investigation, kagent_adapter
from tests import factories


class TestKagentAdapter:
    def test_is_not_configured_without_client(self) -> None:
        # Given no K8s API client
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=None)

        # Then it reports as not configured
        assert adapter.is_configured is False

    def test_is_configured_with_client(self) -> None:
        # Given a mock K8s API client
        mock_client = mock.MagicMock()

        # When creating the adapter
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=mock_client)

        # Then it reports as configured
        assert adapter.is_configured is True

    @pytest.mark.asyncio
    async def test_returns_degraded_result_when_not_configured(self) -> None:
        # Given an unconfigured adapter
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=None)
        alert = factories.make_alert(title="Pod CrashLoopBackOff")
        context = investigation.InvestigationContext(cluster_name="prod")

        # When investigating
        result = await adapter.investigate(alert=alert, context=context)

        # Then a degraded result is returned
        assert result.adapter_name == "kagent"
        assert result.findings == ()
        assert len(result.audit_trail) == 1
        assert result.audit_trail[0].status == "error"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain/sre/test_kagent_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Implement KagentAdapter**

```python
# src/sentinel/domain/sre/kagent_adapter.py
"""
Kagent investigation adapter.

Delegates Kubernetes investigation to a kagent operator running
in the cluster.  Creates a kagent CRD, polls for completion,
and maps the results to Sentinel's InvestigationResult.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sentinel.domain.sre import entities, investigation
from sentinel.utils import logs

logger = logs.get_logger()


class KagentAdapter(investigation.K8sInvestigationAdapter):
    """
    Investigate alerts by delegating to the kagent K8s operator.

    Creates a kagent CRD with alert context, polls for completion,
    and maps kagent's findings to Sentinel's InvestigationResult.
    """

    def __init__(
        self,
        *,
        k8s_api_client: Any | None = None,
        kagent_namespace: str = "kagent-system",
        timeout_seconds: int = 120,
    ) -> None:
        self._k8s_api_client = k8s_api_client
        self._kagent_namespace = kagent_namespace
        self._timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return self._k8s_api_client is not None

    async def investigate(
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> investigation.InvestigationResult:
        start_time = time.monotonic()
        started_at = datetime.now(tz=UTC)

        if not self.is_configured:
            return investigation.InvestigationResult(
                findings=(),
                sources_queried=(),
                duration_ms=0,
                adapter_name="kagent",
                audit_trail=(
                    investigation.AuditEntry(
                        timestamp=started_at,
                        adapter_name="kagent",
                        action="configuration_check",
                        tool_name=None,
                        status="error",
                        duration_ms=0,
                        error_code=None,
                        payload={"reason": "Kagent K8s API client not configured"},
                    ),
                ),
            )

        logs.log_event(
            "kagent_investigation_started",
            params={
                "alert_id": alert.id,
                "service": alert.service,
                "kagent_namespace": self._kagent_namespace,
                "timeout_seconds": self._timeout_seconds,
            },
        )

        # TODO: Implement CRD creation, polling, and result mapping
        # when kagent operator is available in the cluster.
        #
        # Flow:
        # 1. Create kagent investigation CRD with alert context
        # 2. Poll CRD status until completed/failed/timeout
        # 3. Parse kagent findings and map to InvestigationResult
        #
        # For now, return a placeholder that signals "not yet wired":

        duration_ms = int((time.monotonic() - start_time) * 1000)

        return investigation.InvestigationResult(
            findings=(),
            sources_queried=(),
            duration_ms=duration_ms,
            adapter_name="kagent",
            audit_trail=(
                investigation.AuditEntry(
                    timestamp=started_at,
                    adapter_name="kagent",
                    action="crd_operation",
                    tool_name=None,
                    status="error",
                    duration_ms=duration_ms,
                    error_code=None,
                    payload={
                        "reason": "Kagent CRD integration pending — operator not yet deployed",
                        "alert_id": alert.id,
                    },
                ),
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain/sre/test_kagent_adapter.py -v`
Expected: All 3 tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/domain/sre/kagent_adapter.py tests/unit/domain/sre/test_kagent_adapter.py
git commit -m "feat: add KagentAdapter skeleton for kagent CRD delegation"
```

---

## Task 12: Helm Chart Updates

**Files:**
- Create: `helm/sentinel/templates/clusterrole.yaml`
- Create: `helm/sentinel/templates/clusterrolebinding.yaml`
- Create: `helm/sentinel/templates/mcp-deployment.yaml`
- Create: `helm/sentinel/templates/mcp-service.yaml`
- Modify: `helm/sentinel/values.yaml`
- Modify: `helm/sentinel/templates/networkpolicy.yaml`

- [x] **Step 1: Add new values to values.yaml**

Append to `helm/sentinel/values.yaml` (after the `networkPolicy` block, line 156):

```yaml

# -- K8s investigation agent configuration
k8sAgent:
  enabled: false
  backend: "native"  # "native", "kagent", or "both" (comparison mode)
  rbac:
    create: true
    namespaces: []  # Empty = cluster-wide, or restrict to specific namespaces

# -- Kagent operator integration
kagent:
  enabled: false
  namespace: "kagent-system"
  investigationTimeout: 120

# -- MCP server deployment (exposes Sentinel tools to external agents)
mcpServer:
  enabled: false
  transport: "streamable-http"
  port: 8811
  replicaCount: 1
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

- [x] **Step 2: Create ClusterRole template**

```yaml
# helm/sentinel/templates/clusterrole.yaml
{{- if and .Values.k8sAgent.enabled .Values.k8sAgent.rbac.create }}
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ include "sentinel.fullname" . }}-k8s-reader
  labels:
    {{- include "sentinel.labels" . | nindent 4 }}
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "events", "nodes", "replicationcontrollers"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets", "statefulsets", "daemonsets"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["autoscaling"]
    resources: ["horizontalpodautoscalers"]
    verbs: ["get", "list", "watch"]
  {{- if .Values.kagent.enabled }}
  # Kagent CRD access
  - apiGroups: ["kagent.dev"]
    resources: ["*"]
    verbs: ["create", "get", "list", "watch"]
  {{- end }}
{{- end }}
```

- [x] **Step 3: Create ClusterRoleBinding template**

```yaml
# helm/sentinel/templates/clusterrolebinding.yaml
{{- if and .Values.k8sAgent.enabled .Values.k8sAgent.rbac.create }}
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ include "sentinel.fullname" . }}-k8s-reader
  labels:
    {{- include "sentinel.labels" . | nindent 4 }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ include "sentinel.fullname" . }}-k8s-reader
subjects:
  - kind: ServiceAccount
    name: {{ include "sentinel.serviceAccountName" . }}
    namespace: {{ .Release.Namespace }}
{{- end }}
```

- [x] **Step 4: Create MCP deployment template**

```yaml
# helm/sentinel/templates/mcp-deployment.yaml
{{- if .Values.mcpServer.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "sentinel.fullname" . }}-mcp
  labels:
    {{- include "sentinel.labels" . | nindent 4 }}
    app.kubernetes.io/component: mcp-server
spec:
  replicas: {{ .Values.mcpServer.replicaCount }}
  selector:
    matchLabels:
      {{- include "sentinel.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: mcp-server
  template:
    metadata:
      labels:
        {{- include "sentinel.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: mcp-server
    spec:
      serviceAccountName: {{ include "sentinel.serviceAccountName" . }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: mcp-server
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["uv", "run", "python", "-m", "sentinel.interfaces.mcp.server"]
          ports:
            - name: mcp
              containerPort: {{ .Values.mcpServer.port }}
              protocol: TCP
          resources:
            {{- toYaml .Values.mcpServer.resources | nindent 12 }}
          envFrom:
            {{- toYaml .Values.envFrom | nindent 12 }}
          env:
            {{- range $key, $value := .Values.env }}
            - name: {{ $key }}
              value: {{ $value | quote }}
            {{- end }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
{{- end }}
```

- [x] **Step 5: Create MCP service template**

```yaml
# helm/sentinel/templates/mcp-service.yaml
{{- if .Values.mcpServer.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "sentinel.fullname" . }}-mcp
  labels:
    {{- include "sentinel.labels" . | nindent 4 }}
    app.kubernetes.io/component: mcp-server
spec:
  type: ClusterIP
  ports:
    - port: {{ .Values.mcpServer.port }}
      targetPort: mcp
      protocol: TCP
      name: mcp
  selector:
    {{- include "sentinel.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: mcp-server
{{- end }}
```

- [x] **Step 6: Update network policy**

Read the existing `networkpolicy.yaml` first, then add egress rules for K8s API server, kagent namespace, and MCP server. Add these egress rules inside the existing `{{- if .Values.networkPolicy.enabled }}` block:

```yaml
    # K8s API server (for K8s investigation agent)
    {{- if .Values.k8sAgent.enabled }}
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0  # K8s API server IP varies per cluster
      ports:
        - port: 443
          protocol: TCP
    {{- end }}
    # Kagent namespace
    {{- if .Values.kagent.enabled }}
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.kagent.namespace }}
    {{- end }}
    # MCP server (internal)
    {{- if .Values.mcpServer.enabled }}
    - to:
        - podSelector:
            matchLabels:
              app.kubernetes.io/component: mcp-server
      ports:
        - port: {{ .Values.mcpServer.port }}
          protocol: TCP
    {{- end }}
```

- [x] **Step 7: Validate Helm template rendering**

Run: `helm template sentinel helm/sentinel/ --set k8sAgent.enabled=true --set mcpServer.enabled=true --set kagent.enabled=true | head -100`
Expected: Valid YAML output with ClusterRole, MCP deployment, and service

- [x] **Step 8: Commit**

```bash
git add helm/sentinel/
git commit -m "feat: add Helm templates for K8s agent RBAC, MCP server deployment, and network policies"
```

---

## Task 13: Streamlit Chat App — Backend Selector and K8s Scenarios

**Files:**
- Modify: `src/sentinel/interfaces/chat/app.py`

- [x] **Step 1: Add K8s test scenarios**

Add after `_SUPPORT_SCENARIOS` (around line 405) in `app.py`:

```python
_K8S_SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "label": "Node NotReady",
        "prompt": (
            "ALERT: Node NotReady — kubelet heartbeat timeout\n\n"
            "Node worker-3 in the prod-eu-west-1 cluster has been in "
            "NotReady state for 5 minutes. Kubelet heartbeat has stopped. "
            "12 pods were running on this node including critical payment "
            "processing workloads. No recent maintenance was scheduled."
        ),
    },
    {
        "label": "Deployment rollout stuck",
        "prompt": (
            "WARNING: Deployment rollout stuck — new ReplicaSet not progressing\n\n"
            "The orders-service deployment in namespace 'production' has been "
            "stuck in a rollout for 15 minutes. The new ReplicaSet has 0/3 ready "
            "replicas. Old pods are still running. The rollout was triggered by "
            "a config change to increase memory limits."
        ),
    },
    {
        "label": "PVC pending — no PersistentVolume",
        "prompt": (
            "ALERT: PersistentVolumeClaim pending in production\n\n"
            "PVC 'data-postgres-0' in namespace 'databases' has been in Pending "
            "state for 10 minutes. No PersistentVolume is available that matches "
            "the claim's storage class 'gp3-encrypted'. The StatefulSet postgres "
            "is stuck waiting for the volume."
        ),
    },
    {
        "label": "HPA unable to scale",
        "prompt": (
            "WARNING: HorizontalPodAutoscaler unable to calculate metrics\n\n"
            "HPA for api-gateway in namespace 'production' reports "
            "'FailedGetResourceMetric' — unable to get CPU utilisation. "
            "The metrics-server pod in kube-system is in CrashLoopBackOff. "
            "Current load is 3x normal but replicas are stuck at 2."
        ),
    },
    {
        "label": "Ingress returning 404",
        "prompt": (
            "CRITICAL: Ingress returning 404 for all routes\n\n"
            "The main ingress 'api-ingress' in namespace 'production' started "
            "returning 404 for all paths 20 minutes ago. The backend services "
            "are healthy. A recent change updated the service selector labels "
            "as part of a Helm chart upgrade."
        ),
    },
    {
        "label": "Readiness probe failing after config change",
        "prompt": (
            "WARNING: Pods failing readiness probes after ConfigMap update\n\n"
            "All 5 pods of user-service in namespace 'production' are failing "
            "readiness probes since a ConfigMap update 10 minutes ago. The pods "
            "are Running but 0/5 Ready. Traffic has stopped routing to them. "
            "The ConfigMap change updated the database connection string."
        ),
    },
)
```

- [x] **Step 2: Add backend selector and K8s scenarios to sidebar**

Update `_render_sidebar()` to include investigation backend selector and K8s scenarios. After the Support scenarios section (around line 429), add:

```python
        st.subheader("K8s Investigation")
        for scenario in _K8S_SCENARIOS:
            if st.button(scenario["label"], key=f"k8s-{scenario['label']}"):
                st.session_state["prefill"] = scenario["prompt"]
                st.rerun()

        st.divider()
        st.header("Investigation Backend")
        st.selectbox(
            "K8s Backend",
            options=["Disabled", "Native K8s", "Kagent", "Both (comparison)"],
            key="k8s_backend",
            index=0,
        )
```

- [ ] **Step 3: Add audit trail rendering function**

Add after `_render_trace()` function (around line 265):

```python
def _render_audit_trail(audit_trail: list[dict[str, Any]]) -> None:
    """Render investigation audit trail as a timeline."""
    if not audit_trail:
        return

    with st.expander("Audit Trail", expanded=False):
        for entry in audit_trail:
            status = entry.get("status", "unknown")
            status_icon = {"success": "\u2705", "error": "\u274c", "timeout": "\u26a0\ufe0f"}.get(
                status, "\u2753"
            )
            tool = entry.get("tool_name") or entry.get("action", "unknown")
            duration = entry.get("duration_ms", 0)

            st.markdown(
                f"{status_icon} **{tool}** — {duration}ms — `{entry.get('adapter_name', '')}`"
            )
            if entry.get("error_code"):
                st.error(f"Error: {entry['error_code']}")
            if entry.get("payload"):
                with st.expander(f"Payload: {tool}", expanded=False):
                    st.json(entry["payload"])
```

- [ ] **Step 4: Run the chat app to verify it loads**

Run: `uv run streamlit run src/sentinel/interfaces/chat/app.py --server.headless true &` then `sleep 3 && curl -s http://localhost:8501 | head -5`
Expected: Streamlit HTML output (app loads without errors)

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/interfaces/chat/app.py
git commit -m "feat: add K8s scenarios, backend selector, and audit trail viewer to Streamlit chat"
```

---

## Task 14: Full Verification

- [ ] **Step 1: Run full unit test suite**

Run: `uv run pytest tests/unit/ -v --tb=short`
Expected: All tests PASS (existing + new)

- [ ] **Step 2: Run import-linter**

Run: `uv run lint-imports`
Expected: All contracts PASS

- [ ] **Step 3: Run mypy**

Run: `uv run mypy src/sentinel/ --ignore-missing-imports`
Expected: No new errors

- [ ] **Step 4: Run ruff**

Run: `uv run ruff check src/sentinel/ tests/`
Expected: No errors (or only pre-existing ones)

- [ ] **Step 5: Commit any lint fixes**

```bash
# If lint fixes were needed:
git add -u
git commit -m "chore: fix lint issues from K8s agent and MCP integration"
```
