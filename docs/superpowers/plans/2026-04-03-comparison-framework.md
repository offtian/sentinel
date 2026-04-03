# Comparison Framework + Streamlit UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a comparison mode that runs two investigation backends concurrently, persists results to PostgreSQL, scores them with `EvaluationMetrics`, and shows side-by-side results in the Streamlit chat app.

**Architecture:** Extend `ComparisonResult` with a factory that derives metrics from two `InvestigationResult` objects. Add `comparison_runs` and `eval_runs` database tables with persistence via the `databases` library. Wire comparison mode into the `InvestigateWithHolmes` pipeline node. Display audit trails and comparison results in Streamlit.

**Tech Stack:** Python 3.13, attrs, Pydantic Graph, databases (async PostgreSQL), Alembic, Streamlit, pydantic_evals

**Spec:** `docs/superpowers/specs/2026-04-03-comparison-framework-design.md`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `src/sentinel/data/comparison.py` | Persist and fetch comparison runs via `databases` |
| `src/sentinel/data/eval_runs.py` | Persist and fetch eval runs via `databases` |
| `src/sentinel/data/migrations/alembic/versions/002_create_comparison_and_eval_tables.py` | Alembic migration for new tables |
| `src/sentinel/evals/evaluators/comparison_evaluator.py` | K8s investigation evaluators |
| `src/sentinel/evals/cases/k8s_investigation_cases.json` | 3 golden test cases |
| `tests/unit/domain/evaluation/test_comparison_from_results.py` | Tests for `ComparisonResult.from_investigation_results()` |
| `tests/unit/data/test_comparison_persistence.py` | Tests for comparison run persistence |
| `tests/unit/data/test_eval_runs_persistence.py` | Tests for eval run persistence |
| `tests/unit/evals/evaluators/test_comparison_evaluator.py` | Evaluator tests |
| `tests/unit/interfaces/graphs/test_sre_comparison_mode.py` | Comparison pipeline tests |

### Modified files

| File | What changes |
|------|-------------|
| `src/sentinel/domain/evaluation/comparison.py` | Add `from_investigation_results()` factory |
| `src/sentinel/interfaces/graphs/sre_investigation.py` | Add `comparison_result` to `State`, comparison mode in `InvestigateWithHolmes` |
| `src/sentinel/interfaces/chat/app.py` | Audit trail viewer, comparison mode UI |
| `tests/factories/__init__.py` | Add `MockKagentAdapter`, `make_investigation_result()`, `make_audit_entry()` |
| `pyproject.toml` | Add `databases` dependency |

---

## Task 1: Add `databases` Dependency

**Files:**
- Modify: `pyproject.toml`

- [x] **Step 1: Add databases to dependencies**

In `pyproject.toml`, add `"databases[asyncpg]"` to the `dependencies` list (after `"asyncpg"`):

```toml
    "asyncpg",
    "databases[asyncpg]",
```

- [x] **Step 2: Install and verify**

Run: `uv sync`
Expected: Clean install with `databases` available.

- [x] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add databases dependency for async PostgreSQL access"
```

---

## Task 2: Test Factories — MockKagentAdapter and Investigation Result Helpers

**Files:**
- Modify: `tests/factories/__init__.py`
- Test: Verify import works

- [x] **Step 1: Write MockKagentAdapter and factory functions**

Add these to the end of `tests/factories/__init__.py`:

```python
from sentinel.domain.sre import investigation


def make_audit_entry(
    *,
    adapter_name: str = "native_k8s",
    action: str = "tool_call",
    tool_name: str | None = "get_pod_status",
    status: str = "success",
    duration_ms: int = 42,
    error_code: str | None = None,
    payload: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> investigation.AuditEntry:
    return investigation.AuditEntry(
        timestamp=timestamp or datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        adapter_name=adapter_name,
        action=action,
        tool_name=tool_name,
        status=status,
        duration_ms=duration_ms,
        error_code=error_code,
        payload=payload or {},
    )


def make_investigation_result(
    *,
    findings: tuple[sre_entities.Finding, ...] | None = None,
    sources_queried: tuple[str, ...] = ("kubernetes", "datadog_logs"),
    duration_ms: int = 350,
    adapter_name: str = "native_k8s",
    audit_trail: tuple[investigation.AuditEntry, ...] | None = None,
) -> investigation.InvestigationResult:
    return investigation.InvestigationResult(
        findings=findings or (make_finding(source="kubernetes", summary="Pod restarting due to OOMKilled"),),
        sources_queried=sources_queried,
        duration_ms=duration_ms,
        adapter_name=adapter_name,
        audit_trail=audit_trail or (make_audit_entry(adapter_name=adapter_name),),
    )


class MockKagentAdapter(investigation.K8sInvestigationAdapter):
    """Mock kagent adapter for testing comparison mode."""

    def __init__(
        self,
        *,
        findings: tuple[sre_entities.Finding, ...] = (),
        delay_ms: int = 0,
    ) -> None:
        self._findings = findings or (
            make_finding(source="kagent", summary="CRD investigation: pod OOMKilled"),
        )
        self._delay_ms = delay_ms

    @property
    def is_configured(self) -> bool:
        return True

    async def investigate(
        self,
        *,
        alert: sre_entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> investigation.InvestigationResult:
        if self._delay_ms > 0:
            await asyncio.sleep(self._delay_ms / 1000)
        return investigation.InvestigationResult(
            findings=self._findings,
            sources_queried=("kagent_crd",),
            duration_ms=self._delay_ms or 200,
            adapter_name="kagent",
            audit_trail=(
                make_audit_entry(
                    adapter_name="kagent",
                    action="crd_operation",
                    tool_name=None,
                    duration_ms=self._delay_ms or 200,
                ),
            ),
        )
```

Also add the `asyncio` import at the top of the file:

```python
import asyncio
```

- [x] **Step 2: Verify factories import cleanly**

Run: `python -c "from tests.factories import MockKagentAdapter, make_investigation_result, make_audit_entry; print('OK')"`
Expected: `OK`

- [x] **Step 3: Commit**

```bash
git add tests/factories/__init__.py
git commit -m "test: add MockKagentAdapter and investigation result factories"
```

---

## Task 3: ComparisonResult.from_investigation_results()

**Files:**
- Create: `tests/unit/domain/evaluation/test_comparison_from_results.py`
- Modify: `src/sentinel/domain/evaluation/comparison.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/domain/evaluation/test_comparison_from_results.py
from __future__ import annotations

from tests import factories
from sentinel.domain.evaluation import comparison


class TestFromInvestigationResults:
    def test_computes_evidence_source_count(self) -> None:
        # Given two investigation results with different source counts
        baseline = factories.make_investigation_result(
            sources_queried=("datadog_logs", "kubernetes", "pagerduty"),
            adapter_name="holmes",
        )
        challenger = factories.make_investigation_result(
            sources_queried=("kubernetes",),
            adapter_name="native_k8s",
        )

        # When a comparison is created
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline,
            challenger=challenger,
            case_id="test-001",
        )

        # Then evidence source count reflects actual counts
        assert result.baseline.evidence_source_count == 3
        assert result.challenger.evidence_source_count == 1

    def test_computes_latency(self) -> None:
        # Given baseline is slower than challenger
        baseline = factories.make_investigation_result(
            duration_ms=500, adapter_name="holmes",
        )
        challenger = factories.make_investigation_result(
            duration_ms=200, adapter_name="native_k8s",
        )

        # When a comparison is created
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline,
            challenger=challenger,
            case_id="test-002",
        )

        # Then latency metrics reflect duration
        assert result.baseline.latency_p50_ms == 500
        assert result.challenger.latency_p50_ms == 200

    def test_computes_degradation_score_from_audit_trail(self) -> None:
        # Given challenger has one error in two audit entries
        error_entry = factories.make_audit_entry(status="error", adapter_name="native_k8s")
        ok_entry = factories.make_audit_entry(status="success", adapter_name="native_k8s")
        challenger = factories.make_investigation_result(
            adapter_name="native_k8s",
            audit_trail=(ok_entry, error_entry),
        )
        baseline = factories.make_investigation_result(adapter_name="holmes")

        # When a comparison is created
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline,
            challenger=challenger,
            case_id="test-003",
        )

        # Then degradation score is 0.5 (1 error out of 2 entries)
        assert result.challenger.degradation_score == 0.5

    def test_winner_by_dimension_picks_better_value(self) -> None:
        # Given baseline has more sources but is slower
        baseline = factories.make_investigation_result(
            sources_queried=("a", "b", "c"),
            duration_ms=1000,
            adapter_name="holmes",
        )
        challenger = factories.make_investigation_result(
            sources_queried=("a",),
            duration_ms=100,
            adapter_name="native_k8s",
        )

        # When a comparison is created
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline,
            challenger=challenger,
            case_id="test-004",
        )

        # Then winners are correct per dimension
        assert result.winner_by_dimension["evidence_source_count"] == "holmes"
        assert result.winner_by_dimension["latency_p50_ms"] == "native_k8s"

    def test_handles_empty_audit_trail(self) -> None:
        # Given results with no audit trail
        baseline = factories.make_investigation_result(
            audit_trail=(), adapter_name="holmes",
        )
        challenger = factories.make_investigation_result(
            audit_trail=(), adapter_name="native_k8s",
        )

        # When a comparison is created
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline,
            challenger=challenger,
        )

        # Then degradation scores default to 1.0 (no errors)
        assert result.baseline.degradation_score == 1.0
        assert result.challenger.degradation_score == 1.0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `just test tests/unit/domain/evaluation/test_comparison_from_results.py -v`
Expected: FAIL with `AttributeError: type object 'ComparisonResult' has no attribute 'from_investigation_results'`

- [x] **Step 3: Implement from_investigation_results()**

Replace `src/sentinel/domain/evaluation/comparison.py` entirely:

```python
"""
Side-by-side comparison of two investigation backends.
"""

from __future__ import annotations

from collections.abc import Mapping

import attrs

from sentinel.domain.evaluation import metrics
from sentinel.domain.sre import investigation


def _degradation_score(*, audit_trail: tuple[investigation.AuditEntry, ...]) -> float:
    """
    Return 1.0 minus the fraction of error entries in the audit trail.

    Return 1.0 (no degradation) when the trail is empty.
    """
    if not audit_trail:
        return 1.0
    error_count = sum(1 for e in audit_trail if e.status == "error")
    return 1.0 - (error_count / len(audit_trail))


def _evidence_diversity(*, sources: tuple[str, ...]) -> float:
    """
    Return ratio of unique source types to total sources.

    Return 0.0 when there are no sources.
    """
    if not sources:
        return 0.0
    return len(set(sources)) / len(sources)


def _metrics_from_result(
    result: investigation.InvestigationResult,
) -> metrics.EvaluationMetrics:
    """
    Derive evaluation metrics from a single investigation result.

    Metrics that require labelled data (precision, hallucination, brier
    score, robustness) default to 0.0 — they need ground-truth labels
    that a single run cannot provide.
    """
    return metrics.EvaluationMetrics(
        factual_precision=0.0,
        factual_recall=0.0,
        hallucination_rate=0.0,
        latency_p50_ms=result.duration_ms,
        latency_p95_ms=result.duration_ms,
        latency_p99_ms=result.duration_ms,
        confidence_brier_score=0.0,
        evidence_source_count=len(result.sources_queried),
        evidence_diversity=_evidence_diversity(sources=result.sources_queried),
        robustness_variance=0.0,
        degradation_score=_degradation_score(audit_trail=result.audit_trail),
        token_cost=0,
    )


def _pick_winner(
    *,
    baseline_val: int | float,
    challenger_val: int | float,
    baseline_name: str,
    challenger_name: str,
    lower_is_better: bool = False,
) -> str:
    """
    Return the adapter name with the better value.

    Return "tie" when values are equal.
    """
    if baseline_val == challenger_val:
        return "tie"
    if lower_is_better:
        return baseline_name if baseline_val < challenger_val else challenger_name
    return baseline_name if baseline_val > challenger_val else challenger_name


@attrs.frozen
class ComparisonResult:
    """
    Compare baseline vs challenger across all evaluation dimensions.
    """

    case_id: str
    baseline: metrics.EvaluationMetrics
    challenger: metrics.EvaluationMetrics
    winner_by_dimension: Mapping[str, str]

    @staticmethod
    def from_investigation_results(
        *,
        baseline: investigation.InvestigationResult,
        challenger: investigation.InvestigationResult,
        case_id: str = "",
    ) -> ComparisonResult:
        """
        Build a comparison from two investigation results.

        Derive metrics from each result and determine winners
        per dimension.

        :param baseline: The primary/reference investigation result.
        :param challenger: The secondary investigation result to compare.
        :param case_id: Optional identifier for the test case.
        """
        baseline_metrics = _metrics_from_result(baseline)
        challenger_metrics = _metrics_from_result(challenger)

        # Dimensions where higher is better
        higher_better = (
            "evidence_source_count",
            "evidence_diversity",
            "degradation_score",
        )
        # Dimensions where lower is better
        lower_better = (
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_p99_ms",
            "token_cost",
            "hallucination_rate",
        )

        winners: dict[str, str] = {}
        for dim in higher_better:
            b_val = getattr(baseline_metrics, dim)
            c_val = getattr(challenger_metrics, dim)
            if b_val != 0 or c_val != 0:
                winners[dim] = _pick_winner(
                    baseline_val=b_val,
                    challenger_val=c_val,
                    baseline_name=baseline.adapter_name,
                    challenger_name=challenger.adapter_name,
                )
        for dim in lower_better:
            b_val = getattr(baseline_metrics, dim)
            c_val = getattr(challenger_metrics, dim)
            if b_val != 0 or c_val != 0:
                winners[dim] = _pick_winner(
                    baseline_val=b_val,
                    challenger_val=c_val,
                    baseline_name=baseline.adapter_name,
                    challenger_name=challenger.adapter_name,
                    lower_is_better=True,
                )

        return ComparisonResult(
            case_id=case_id,
            baseline=baseline_metrics,
            challenger=challenger_metrics,
            winner_by_dimension=winners,
        )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `just test tests/unit/domain/evaluation/test_comparison_from_results.py -v`
Expected: All 5 tests PASS.

- [x] **Step 5: Run existing comparison tests to verify nothing broke**

Run: `just test tests/unit/domain/evaluation/ -v`
Expected: All tests PASS (existing `test_comparison.py` + new tests).

- [x] **Step 6: Commit**

```bash
git add src/sentinel/domain/evaluation/comparison.py tests/unit/domain/evaluation/test_comparison_from_results.py
git commit -m "feat: add ComparisonResult.from_investigation_results() factory"
```

---

## Task 4: Alembic Migration — comparison_runs and eval_runs Tables

**Files:**
- Create: `src/sentinel/data/migrations/alembic/versions/002_create_comparison_and_eval_tables.py`

- [x] **Step 1: Write the migration**

```python
# src/sentinel/data/migrations/alembic/versions/002_create_comparison_and_eval_tables.py
"""
Create comparison_runs and eval_runs tables.

Revision ID: 002
Revises: 001
Create Date: 2026-04-03

Adds two tables for investigation comparison and evaluation tracking:
- comparison_runs: side-by-side investigation backend results
- eval_runs: evaluation framework execution records
"""

import sqlalchemy as sa
from alembic import op


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- comparison_runs --
    op.create_table(
        "comparison_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("investigation_record_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("baseline_adapter", sa.String(), nullable=False),
        sa.Column("challenger_adapter", sa.String(), nullable=False),
        sa.Column("baseline_result_json", sa.JSON(), nullable=False),
        sa.Column("challenger_result_json", sa.JSON(), nullable=False),
        sa.Column("comparison_result_json", sa.JSON(), nullable=False),
        sa.Column("baseline_duration_ms", sa.Integer(), nullable=False),
        sa.Column("challenger_duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- eval_runs --
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_name", sa.String(), nullable=False, index=True),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("passed_cases", sa.Integer(), nullable=False),
        sa.Column("failed_cases", sa.Integer(), nullable=False),
        sa.Column("average_score", sa.Float(), nullable=True),
        sa.Column("results_json", sa.JSON(), nullable=False),
        sa.Column("run_duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_table("comparison_runs")
```

- [x] **Step 2: Verify migration syntax**

Run: `python -c "import importlib; m = importlib.import_module('sentinel.data.migrations.alembic.versions.002_create_comparison_and_eval_tables'); print('OK')"`
Expected: `OK`

- [x] **Step 3: Commit**

```bash
git add src/sentinel/data/migrations/alembic/versions/002_create_comparison_and_eval_tables.py
git commit -m "feat: add migration for comparison_runs and eval_runs tables"
```

---

## Task 5: Comparison Run Persistence

**Files:**
- Create: `tests/unit/data/test_comparison_persistence.py`
- Create: `src/sentinel/data/comparison.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/data/test_comparison_persistence.py
from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import comparison as comparison_persistence


class TestPersistComparisonRun:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        investigation_record_id = uuid.uuid4()
        baseline_json = {"adapter_name": "holmes", "duration_ms": 500}
        challenger_json = {"adapter_name": "native_k8s", "duration_ms": 200}
        comparison_json = {"case_id": "test", "winner_by_dimension": {}}

        # When a comparison run is persisted
        result_id = await comparison_persistence.persist_comparison_run(
            db=mock_db,
            investigation_record_id=investigation_record_id,
            baseline_adapter="holmes",
            challenger_adapter="native_k8s",
            baseline_result_json=baseline_json,
            challenger_result_json=challenger_json,
            comparison_result_json=comparison_json,
            baseline_duration_ms=500,
            challenger_duration_ms=200,
        )

        # Then a UUID is returned and execute was called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_by_investigation_id(self) -> None:
        # Given a mock database with one row
        mock_db = mock.AsyncMock()
        row_id = uuid.uuid4()
        investigation_id = uuid.uuid4()
        mock_db.fetch_all.return_value = [
            {
                "id": row_id,
                "investigation_record_id": investigation_id,
                "baseline_adapter": "holmes",
                "challenger_adapter": "native_k8s",
                "baseline_duration_ms": 500,
                "challenger_duration_ms": 200,
                "created_at": "2026-04-03T12:00:00+00:00",
            },
        ]

        # When fetching by investigation record id
        rows = await comparison_persistence.fetch_comparison_runs(
            db=mock_db,
            investigation_record_id=investigation_id,
        )

        # Then one row is returned
        assert len(rows) == 1
        assert rows[0]["baseline_adapter"] == "holmes"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `just test tests/unit/data/test_comparison_persistence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.data.comparison'`

- [x] **Step 3: Implement persistence module**

```python
# src/sentinel/data/comparison.py
"""
Persist and fetch comparison run records via the databases library.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases


async def persist_comparison_run(
    *,
    db: databases.Database,
    investigation_record_id: uuid.UUID,
    baseline_adapter: str,
    challenger_adapter: str,
    baseline_result_json: dict[str, Any],
    challenger_result_json: dict[str, Any],
    comparison_result_json: dict[str, Any],
    baseline_duration_ms: int,
    challenger_duration_ms: int,
) -> uuid.UUID:
    """
    Insert a comparison run record.

    :param db: The async database connection.
    :param investigation_record_id: FK to the investigation_records table.
    :param baseline_adapter: Name of the baseline adapter (e.g. "holmes").
    :param challenger_adapter: Name of the challenger adapter (e.g. "native_k8s").
    :param baseline_result_json: Serialised baseline InvestigationResult.
    :param challenger_result_json: Serialised challenger InvestigationResult.
    :param comparison_result_json: Serialised ComparisonResult with metrics.
    :param baseline_duration_ms: Baseline investigation duration.
    :param challenger_duration_ms: Challenger investigation duration.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    query = """
        INSERT INTO comparison_runs (
            id, investigation_record_id, baseline_adapter, challenger_adapter,
            baseline_result_json, challenger_result_json, comparison_result_json,
            baseline_duration_ms, challenger_duration_ms
        ) VALUES (
            :id, :investigation_record_id, :baseline_adapter, :challenger_adapter,
            :baseline_result_json, :challenger_result_json, :comparison_result_json,
            :baseline_duration_ms, :challenger_duration_ms
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "investigation_record_id": investigation_record_id,
            "baseline_adapter": baseline_adapter,
            "challenger_adapter": challenger_adapter,
            "baseline_result_json": baseline_result_json,
            "challenger_result_json": challenger_result_json,
            "comparison_result_json": comparison_result_json,
            "baseline_duration_ms": baseline_duration_ms,
            "challenger_duration_ms": challenger_duration_ms,
        },
    )
    return row_id


async def fetch_comparison_runs(
    *,
    db: databases.Database,
    investigation_record_id: uuid.UUID,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Fetch comparison runs for a given investigation record.

    :param db: The async database connection.
    :param investigation_record_id: FK to filter by.
    :param limit: Maximum rows to return.
    :returns: List of row dicts.
    """
    query = """
        SELECT id, investigation_record_id, baseline_adapter, challenger_adapter,
               baseline_duration_ms, challenger_duration_ms, created_at
        FROM comparison_runs
        WHERE investigation_record_id = :investigation_record_id
        ORDER BY created_at DESC
        LIMIT :limit
    """
    rows = await db.fetch_all(
        query=query,
        values={
            "investigation_record_id": investigation_record_id,
            "limit": limit,
        },
    )
    return [dict(row._mapping) for row in rows]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `just test tests/unit/data/test_comparison_persistence.py -v`
Expected: All 2 tests PASS.

- [x] **Step 5: Commit**

```bash
git add src/sentinel/data/comparison.py tests/unit/data/test_comparison_persistence.py
git commit -m "feat: add comparison run persistence via databases library"
```

---

## Task 6: Eval Run Persistence

**Files:**
- Create: `tests/unit/data/test_eval_runs_persistence.py`
- Create: `src/sentinel/data/eval_runs.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/data/test_eval_runs_persistence.py
from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import eval_runs


class TestPersistEvalRun:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When an eval run is persisted
        result_id = await eval_runs.persist_eval_run(
            db=mock_db,
            dataset_name="k8s_investigation",
            total_cases=3,
            passed_cases=2,
            failed_cases=1,
            average_score=0.75,
            results_json={"cases": [{"id": "1", "passed": True}]},
            run_duration_ms=4200,
        )

        # Then a UUID is returned and execute was called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()


class TestFetchEvalRuns:
    @pytest.mark.asyncio
    async def test_returns_rows_for_dataset(self) -> None:
        # Given a mock database with rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = [
            {
                "id": uuid.uuid4(),
                "dataset_name": "k8s_investigation",
                "total_cases": 3,
                "passed_cases": 3,
                "failed_cases": 0,
                "average_score": 0.9,
                "run_duration_ms": 3000,
                "created_at": "2026-04-03T12:00:00+00:00",
            },
        ]

        # When fetching by dataset name
        rows = await eval_runs.fetch_eval_runs(
            db=mock_db,
            dataset_name="k8s_investigation",
        )

        # Then one row is returned
        assert len(rows) == 1
        assert rows[0]["dataset_name"] == "k8s_investigation"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `just test tests/unit/data/test_eval_runs_persistence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sentinel.data.eval_runs'`

- [x] **Step 3: Implement eval runs persistence**

```python
# src/sentinel/data/eval_runs.py
"""
Persist and fetch evaluation run records via the databases library.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases


async def persist_eval_run(
    *,
    db: databases.Database,
    dataset_name: str,
    total_cases: int,
    passed_cases: int,
    failed_cases: int,
    average_score: float | None,
    results_json: dict[str, Any],
    run_duration_ms: int,
) -> uuid.UUID:
    """
    Insert an evaluation run record.

    :param db: The async database connection.
    :param dataset_name: Name of the evaluation dataset.
    :param total_cases: Total test cases evaluated.
    :param passed_cases: Cases that passed all assertions.
    :param failed_cases: Cases with at least one failing assertion.
    :param average_score: Mean score across all cases (nullable).
    :param results_json: Full per-case results payload.
    :param run_duration_ms: Total evaluation run duration.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    query = """
        INSERT INTO eval_runs (
            id, dataset_name, total_cases, passed_cases, failed_cases,
            average_score, results_json, run_duration_ms
        ) VALUES (
            :id, :dataset_name, :total_cases, :passed_cases, :failed_cases,
            :average_score, :results_json, :run_duration_ms
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "dataset_name": dataset_name,
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "average_score": average_score,
            "results_json": results_json,
            "run_duration_ms": run_duration_ms,
        },
    )
    return row_id


async def fetch_eval_runs(
    *,
    db: databases.Database,
    dataset_name: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Fetch recent evaluation runs for a dataset.

    :param db: The async database connection.
    :param dataset_name: Dataset name to filter by.
    :param limit: Maximum rows to return.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = """
        SELECT id, dataset_name, total_cases, passed_cases, failed_cases,
               average_score, run_duration_ms, created_at
        FROM eval_runs
        WHERE dataset_name = :dataset_name
        ORDER BY created_at DESC
        LIMIT :limit
    """
    rows = await db.fetch_all(
        query=query,
        values={"dataset_name": dataset_name, "limit": limit},
    )
    return [dict(row._mapping) for row in rows]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `just test tests/unit/data/test_eval_runs_persistence.py -v`
Expected: All 2 tests PASS.

- [x] **Step 5: Commit**

```bash
git add src/sentinel/data/eval_runs.py tests/unit/data/test_eval_runs_persistence.py
git commit -m "feat: add eval run persistence via databases library"
```

---

## Task 7: Comparison Mode in Pipeline

**Files:**
- Create: `tests/unit/interfaces/graphs/test_sre_comparison_mode.py`
- Modify: `src/sentinel/interfaces/graphs/sre_investigation.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/interfaces/graphs/test_sre_comparison_mode.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.sre import entities as sre_entities
from sentinel.interfaces.graphs import sre_investigation
from tests import factories


class TestComparisonModeInPipeline:
    @pytest.mark.asyncio
    async def test_comparison_result_stored_on_state_when_challenger_provided(self) -> None:
        # Given an investigation state with an alert and a challenger adapter
        alert = factories.make_alert()
        challenger = factories.MockKagentAdapter()

        state = sre_investigation.State(alert=alert)
        deps = sre_investigation.Dependencies(
            status_update_client=mock.AsyncMock(),
            classifier_model="test",
            analyser_model="test",
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
            challenger_adapter=challenger,
        )
        ctx = mock.MagicMock()
        ctx.state = state
        ctx.deps = deps

        node = sre_investigation.InvestigateWithHolmes()

        # When the node runs
        next_node = await node.run(ctx)

        # Then a comparison result is stored on state
        assert ctx.state.comparison_result is not None
        assert ctx.state.comparison_result.case_id == alert.id

    @pytest.mark.asyncio
    async def test_no_comparison_when_challenger_is_none(self) -> None:
        # Given no challenger adapter
        alert = factories.make_alert()
        state = sre_investigation.State(alert=alert)
        deps = sre_investigation.Dependencies(
            status_update_client=mock.AsyncMock(),
            classifier_model="test",
            analyser_model="test",
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
        )
        ctx = mock.MagicMock()
        ctx.state = state
        ctx.deps = deps

        node = sre_investigation.InvestigateWithHolmes()

        # When the node runs
        await node.run(ctx)

        # Then no comparison result is stored
        assert ctx.state.comparison_result is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `just test tests/unit/interfaces/graphs/test_sre_comparison_mode.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'challenger_adapter'`

- [x] **Step 3: Add comparison mode to the pipeline**

In `src/sentinel/interfaces/graphs/sre_investigation.py`, make these changes:

Add import at the top (after existing imports):
```python
from sentinel.domain.evaluation import comparison
from sentinel.domain.sre import investigation
```

Add `challenger_adapter` to `Dependencies`:
```python
@dataclasses.dataclass
class Dependencies:
    status_update_client: common.StatusUpdateClient
    classifier_model: str
    analyser_model: str
    holmes: holmes_adapter.BaseHolmesAdapter
    pagerduty_client: PagerDutyClient | None = None
    post_to_slack: bool = True
    persist_fn: common.PersistInvestigationFn | None = None
    trace_collector: common.TraceCollector | None = None
    require_approval_below: float = 0.0  # 0 = never require approval
    request_approval_fn: common.RequestApprovalFn | None = None
    # Toolsets injected at agent.run() time.  Built by config.py.
    analyser_toolsets: Sequence[AbstractToolset[object]] = ()
    # Optional challenger adapter for comparison mode.
    challenger_adapter: investigation.BaseInvestigationAdapter | None = None
```

Add `comparison_result` to `State`:
```python
@dataclasses.dataclass
class State:
    alert: sre_entities.Alert
    investigation: sre_entities.Investigation | None = None
    comparison_result: comparison.ComparisonResult | None = None
```

Replace the `InvestigateWithHolmes.run()` method:
```python
@dataclasses.dataclass
class InvestigateWithHolmes(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Run HolmesGPT investigation to gather context from observability systems."""

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> AnalyseRootCause:
        await ctx.deps.status_update_client.update_status(
            "Investigating with observability tools..."
        )

        try:
            holmes_result = await ctx.deps.holmes.investigate(alert=ctx.state.alert)
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"alert_id": ctx.state.alert.id, "node": "InvestigateWithHolmes"},
            )
            return AnalyseRootCause(
                holmes_analysis="Observability investigation unavailable — proceeding with alert context only.",
                holmes_tool_calls=[],
                holmes_sources=[],
            )

        # Run challenger adapter concurrently if configured (comparison mode)
        if ctx.deps.challenger_adapter is not None:
            try:
                challenger_result = await ctx.deps.challenger_adapter.investigate(
                    alert=ctx.state.alert,
                )
                # Build a baseline InvestigationResult from Holmes output
                baseline_result = investigation.InvestigationResult(
                    findings=tuple(
                        sre_entities.Finding(source=s, summary="", relevance=0.5)
                        for s in holmes_result.sources_queried
                    ),
                    sources_queried=tuple(holmes_result.sources_queried),
                    duration_ms=0,
                    adapter_name="holmes",
                )
                ctx.state.comparison_result = comparison.ComparisonResult.from_investigation_results(
                    baseline=baseline_result,
                    challenger=challenger_result,
                    case_id=ctx.state.alert.id,
                )
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={
                        "alert_id": ctx.state.alert.id,
                        "node": "InvestigateWithHolmes",
                        "comparison": "challenger_failed",
                    },
                )

        logs.log_event(
            "holmes_investigation_completed",
            params={
                "alert_id": ctx.state.alert.id,
                "sources_queried": holmes_result.sources_queried,
                "tool_calls_count": len(holmes_result.tool_calls),
            },
        )

        return AnalyseRootCause(
            holmes_analysis=holmes_result.analysis,
            holmes_tool_calls=holmes_result.tool_calls,
            holmes_sources=holmes_result.sources_queried,
        )
```

Also update `investigate_alert()` to accept the challenger:
```python
async def investigate_alert(
    alert: sre_entities.Alert,
    *,
    holmes: holmes_adapter.BaseHolmesAdapter,
    status_update_client: common.StatusUpdateClient | None = None,
    classifier_model: str = "",
    analyser_model: str = "",
    pagerduty_client: PagerDutyClient | None = None,
    post_to_slack: bool = True,
    persist_fn: common.PersistInvestigationFn | None = None,
    trace_collector: common.TraceCollector | None = None,
    require_approval_below: float = 0.0,
    request_approval_fn: common.RequestApprovalFn | None = None,
    analyser_toolsets: Sequence[AbstractToolset[object]] = (),
    challenger_adapter: investigation.BaseInvestigationAdapter | None = None,
) -> common.InvestigationReply:
```

And pass it through to `Dependencies`:
```python
    dependencies = Dependencies(
        ...
        analyser_toolsets=analyser_toolsets,
        challenger_adapter=challenger_adapter,
    )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `just test tests/unit/interfaces/graphs/test_sre_comparison_mode.py -v`
Expected: All 2 tests PASS.

- [x] **Step 5: Run existing pipeline tests**

Run: `just test tests/unit/interfaces/graphs/test_sre_investigation.py -v`
Expected: All existing tests still PASS (challenger_adapter defaults to None).

- [x] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/graphs/sre_investigation.py tests/unit/interfaces/graphs/test_sre_comparison_mode.py
git commit -m "feat: add comparison mode to SRE investigation pipeline"
```

---

## Task 8: Golden Test Cases

**Files:**
- Create: `src/sentinel/evals/cases/k8s_investigation_cases.json`

- [x] **Step 1: Create golden test cases**

```json
[
    {
        "name": "CrashLoopBackOff - OOMKilled",
        "inputs": {
            "agent_name": "k8s_investigation",
            "case_payload": {
                "alert": {
                    "id": "eval-001",
                    "source": "pagerduty",
                    "title": "Pod api-gateway-5f8c9 in CrashLoopBackOff",
                    "description": "Container 'api' in pod api-gateway-5f8c9 has been OOMKilled 5 times in the last 10 minutes. Memory limit is 256Mi.",
                    "severity": "high",
                    "service": "api-gateway"
                },
                "expected_keywords": ["OOMKilled", "memory", "restart"],
                "min_findings_count": 1,
                "max_latency_ms": 5000
            }
        },
        "expected_output": "Investigation should identify OOMKilled as root cause with memory remediation"
    },
    {
        "name": "Node NotReady - kubelet heartbeat",
        "inputs": {
            "agent_name": "k8s_investigation",
            "case_payload": {
                "alert": {
                    "id": "eval-002",
                    "source": "datadog",
                    "title": "Node ip-10-0-1-42 is NotReady",
                    "description": "Node ip-10-0-1-42 has not sent a heartbeat in 5 minutes. kubelet is unresponsive. 12 pods affected.",
                    "severity": "critical",
                    "service": "kubernetes-cluster"
                },
                "expected_keywords": ["NotReady", "kubelet", "heartbeat"],
                "min_findings_count": 1,
                "max_latency_ms": 5000
            }
        },
        "expected_output": "Investigation should identify kubelet failure as root cause"
    },
    {
        "name": "Deployment rollout stuck",
        "inputs": {
            "agent_name": "k8s_investigation",
            "case_payload": {
                "alert": {
                    "id": "eval-003",
                    "source": "pagerduty",
                    "title": "Deployment payments-service rollout stuck",
                    "description": "Deployment payments-service has been progressing for 15 minutes. New ReplicaSet has 0/3 ready pods. Old ReplicaSet still running 3/3.",
                    "severity": "high",
                    "service": "payments-service"
                },
                "expected_keywords": ["rollout", "ReplicaSet", "scaling"],
                "min_findings_count": 1,
                "max_latency_ms": 5000
            }
        },
        "expected_output": "Investigation should identify stuck rollout with ReplicaSet analysis"
    }
]
```

- [x] **Step 2: Verify JSON is valid**

Run: `python -c "import json, pathlib; json.loads(pathlib.Path('src/sentinel/evals/cases/k8s_investigation_cases.json').read_text()); print('OK')"`
Expected: `OK`

- [x] **Step 3: Commit**

```bash
git add src/sentinel/evals/cases/k8s_investigation_cases.json
git commit -m "test: add 3 golden test cases for K8s investigation evaluation"
```

---

## Task 9: Comparison Evaluator

**Files:**
- Create: `tests/unit/evals/evaluators/test_comparison_evaluator.py`
- Create: `src/sentinel/evals/evaluators/comparison_evaluator.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/unit/evals/evaluators/test_comparison_evaluator.py
from __future__ import annotations

from unittest import mock

import pytest

from sentinel.evals import types
from sentinel.evals.evaluators import comparison_evaluator


class TestFindingsKeywordCoverage:
    @pytest.mark.asyncio
    async def test_passes_when_all_keywords_found(self) -> None:
        # Given an evaluator checking for OOM-related keywords
        evaluator = comparison_evaluator.FindingsKeywordCoverage(
            field_path="alert.description",
            keywords=("OOMKilled", "memory"),
            threshold=0.8,
        )
        ctx = mock.MagicMock()
        ctx.inputs = types.InputData(
            agent_name="k8s_investigation",
            case_payload={
                "alert": {"description": "Pod OOMKilled due to memory pressure"},
            },
        )
        ctx.output = ""

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is True

    @pytest.mark.asyncio
    async def test_fails_when_keywords_missing(self) -> None:
        # Given an evaluator checking for keywords not in the text
        evaluator = comparison_evaluator.FindingsKeywordCoverage(
            field_path="alert.description",
            keywords=("OOMKilled", "memory", "restart"),
            threshold=0.8,
        )
        ctx = mock.MagicMock()
        ctx.inputs = types.InputData(
            agent_name="k8s_investigation",
            case_payload={
                "alert": {"description": "Pod is running normally"},
            },
        )
        ctx.output = ""

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is False


class TestMinimumSourceCount:
    @pytest.mark.asyncio
    async def test_passes_when_enough_sources(self) -> None:
        # Given an evaluator requiring at least 1 finding
        evaluator = comparison_evaluator.MinimumSourceCount(
            field_path="min_findings_count",
            actual_count_field="actual_findings_count",
        )
        ctx = mock.MagicMock()
        ctx.inputs = types.InputData(
            agent_name="k8s_investigation",
            case_payload={
                "min_findings_count": 1,
                "actual_findings_count": 3,
            },
        )
        ctx.output = ""

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is True


class TestLatencyThreshold:
    @pytest.mark.asyncio
    async def test_passes_when_under_threshold(self) -> None:
        # Given an evaluator with a 5000ms threshold
        evaluator = comparison_evaluator.LatencyThreshold(
            threshold_field="max_latency_ms",
            actual_field="actual_latency_ms",
        )
        ctx = mock.MagicMock()
        ctx.inputs = types.InputData(
            agent_name="k8s_investigation",
            case_payload={
                "max_latency_ms": 5000,
                "actual_latency_ms": 3200,
            },
        )
        ctx.output = ""

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is True

    @pytest.mark.asyncio
    async def test_fails_when_over_threshold(self) -> None:
        # Given a latency that exceeds the threshold
        evaluator = comparison_evaluator.LatencyThreshold(
            threshold_field="max_latency_ms",
            actual_field="actual_latency_ms",
        )
        ctx = mock.MagicMock()
        ctx.inputs = types.InputData(
            agent_name="k8s_investigation",
            case_payload={
                "max_latency_ms": 5000,
                "actual_latency_ms": 7500,
            },
        )
        ctx.output = ""

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        pass_key = [k for k in result if k.endswith("_pass")][0]
        assert result[pass_key].value is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `just test tests/unit/evals/evaluators/test_comparison_evaluator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement the evaluators**

```python
# src/sentinel/evals/evaluators/comparison_evaluator.py
"""
Evaluators for K8s investigation comparison.

Three evaluators assess investigation quality:
- FindingsKeywordCoverage: expected keywords appear in text fields
- MinimumSourceCount: enough findings/sources were produced
- LatencyThreshold: investigation completed within time budget
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types


def _resolve_field(*, payload: dict[str, Any], field_path: str) -> Any:
    """
    Traverse a nested dict using a dot-separated field path.

    :raises KeyError: if any segment is missing.
    """
    current: Any = payload
    for segment in field_path.split("."):
        current = current[segment]
    return current


@dataclasses.dataclass
class FindingsKeywordCoverage(evaluators.Evaluator):
    """
    Check what fraction of expected keywords appear in a text field.

    Produce a pass/fail assertion based on a configurable threshold.
    """

    field_path: str = ""
    keywords: tuple[str, ...] = ()
    threshold: float = 0.5

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """
        Compute keyword coverage and return a pass/fail assertion.
        """
        payload = ctx.inputs.case_payload
        text = str(_resolve_field(payload=payload, field_path=self.field_path))
        text_lower = text.lower()

        if not self.keywords:
            coverage = 1.0
        else:
            matched = sum(1 for kw in self.keywords if kw.lower() in text_lower)
            coverage = matched / len(self.keywords)

        passed = coverage >= self.threshold
        reason = (
            f"Keyword coverage: {coverage:.2f} "
            f"(threshold: {self.threshold:.2f}, "
            f"matched: {int(coverage * len(self.keywords))}/{len(self.keywords)})"
        )

        name = self.get_default_evaluation_name()
        return {
            f"{name}_pass": evaluator.EvaluationReason(value=passed, reason=reason),
        }


@dataclasses.dataclass
class MinimumSourceCount(evaluators.Evaluator):
    """
    Check that the actual findings/source count meets the expected minimum.
    """

    field_path: str = "min_findings_count"
    actual_count_field: str = "actual_findings_count"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """
        Compare actual count against minimum required.
        """
        payload = ctx.inputs.case_payload
        minimum = int(_resolve_field(payload=payload, field_path=self.field_path))
        actual = int(_resolve_field(payload=payload, field_path=self.actual_count_field))
        passed = actual >= minimum
        reason = f"Source count: {actual} (minimum: {minimum})"

        name = self.get_default_evaluation_name()
        return {
            f"{name}_pass": evaluator.EvaluationReason(value=passed, reason=reason),
        }


@dataclasses.dataclass
class LatencyThreshold(evaluators.Evaluator):
    """
    Check that actual latency is within the threshold.
    """

    threshold_field: str = "max_latency_ms"
    actual_field: str = "actual_latency_ms"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """
        Compare actual latency against maximum allowed.
        """
        payload = ctx.inputs.case_payload
        threshold = int(_resolve_field(payload=payload, field_path=self.threshold_field))
        actual = int(_resolve_field(payload=payload, field_path=self.actual_field))
        passed = actual <= threshold
        reason = f"Latency: {actual}ms (threshold: {threshold}ms)"

        name = self.get_default_evaluation_name()
        return {
            f"{name}_pass": evaluator.EvaluationReason(value=passed, reason=reason),
        }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `just test tests/unit/evals/evaluators/test_comparison_evaluator.py -v`
Expected: All 5 tests PASS.

- [x] **Step 5: Commit**

```bash
git add src/sentinel/evals/evaluators/comparison_evaluator.py src/sentinel/evals/cases/k8s_investigation_cases.json tests/unit/evals/evaluators/test_comparison_evaluator.py
git commit -m "feat: add K8s investigation evaluators and golden test cases"
```

---

## Task 10: Streamlit Audit Trail Viewer

**Files:**
- Modify: `src/sentinel/interfaces/chat/app.py`

- [x] **Step 1: Add the audit trail rendering function**

Add this function after `_render_scenario_buttons()` (around line 674) in `app.py`:

```python
def _render_audit_trail(audit_trail: list[dict[str, Any]]) -> None:
    """Render an audit trail as an expandable timeline."""
    if not audit_trail:
        st.caption("No audit trail entries.")
        return

    for entry in audit_trail:
        status = entry.get("status", "unknown")
        if status == "success":
            badge = ":green[OK]"
        elif status == "error":
            badge = ":red[ERR]"
        else:
            badge = ":orange[???]"

        tool = entry.get("tool_name") or entry.get("action", "unknown")
        duration = entry.get("duration_ms", 0)
        timestamp = entry.get("timestamp", "")

        st.markdown(f"{badge} **{tool}** — {duration}ms | {timestamp}")

        payload = entry.get("payload")
        if payload:
            with st.expander("Payload", expanded=False):
                st.json(payload)
```

- [x] **Step 2: Wire audit trail into SRE result rendering**

Find where the K8s result is displayed in the chat output (after `_run_sre` is called, where `last_k8s_result` is used). Add an audit trail expander after the K8s results section. Locate the section around line 185-204 where `st.session_state["last_k8s_result"]` is set, then find where results are rendered and add:

```python
# In the result rendering section, after showing K8s findings:
k8s_data = st.session_state.get("last_k8s_result")
if k8s_data:
    with st.expander("K8s Investigation Audit Trail", expanded=False):
        _render_audit_trail(k8s_data.get("audit_trail", []))
```

- [x] **Step 3: Verify the app loads without errors**

Run: `python -c "import sentinel.interfaces.chat.app; print('OK')"`
Expected: `OK` (no import errors)

- [x] **Step 4: Commit**

```bash
git add src/sentinel/interfaces/chat/app.py
git commit -m "feat: add audit trail viewer to Streamlit chat app"
```

---

## Task 11: Streamlit Comparison Mode UI

**Files:**
- Modify: `src/sentinel/interfaces/chat/app.py`

- [x] **Step 1: Add comparison mode rendering function**

Add this function after `_render_audit_trail()`:

```python
def _render_comparison(
    holmes_reply: common.InvestigationReply,
    k8s_data: dict[str, Any],
) -> None:
    """Render side-by-side comparison of two investigation backends."""
    st.subheader("Backend Comparison")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("### Holmes (baseline)")
        st.metric("Duration", f"{holmes_reply.confidence.total if holmes_reply.confidence else 'N/A'}")
        st.markdown(f"**Sources:** {', '.join(holmes_reply.sources_queried)}")
        if holmes_reply.findings_summary:
            st.markdown("**Findings:**")
            st.markdown(holmes_reply.findings_summary)

    with col_right:
        adapter = k8s_data.get("adapter_name", "K8s")
        st.markdown(f"### {adapter} (challenger)")
        duration = k8s_data.get("duration_ms", 0)
        st.metric("Duration", f"{duration}ms")
        sources = k8s_data.get("sources_queried", [])
        st.markdown(f"**Sources:** {', '.join(sources)}")
        findings = k8s_data.get("findings", [])
        if findings:
            st.markdown("**Findings:**")
            for finding in findings:
                st.markdown(f"- {finding}")

    # Summary row
    st.divider()
    holmes_sources = len(holmes_reply.sources_queried)
    k8s_sources = len(k8s_data.get("sources_queried", []))
    k8s_duration = k8s_data.get("duration_ms", 0)

    summary_parts = []
    if k8s_duration > 0:
        summary_parts.append(f"**Faster:** {k8s_data.get('adapter_name', 'K8s')} ({k8s_duration}ms)")
    if holmes_sources != k8s_sources:
        more_sources = "Holmes" if holmes_sources > k8s_sources else k8s_data.get("adapter_name", "K8s")
        summary_parts.append(f"**More sources:** {more_sources}")

    if summary_parts:
        st.markdown(" | ".join(summary_parts))
```

- [x] **Step 2: Wire comparison mode into the result rendering**

In the section where SRE results are displayed (after `_run_sre()` returns), add a check for comparison mode:

```python
# After displaying the main Holmes result:
backend = st.session_state.get("k8s_backend", "Disabled")
k8s_data = st.session_state.get("last_k8s_result")

if backend == "Both (comparison)" and k8s_data:
    _render_comparison(reply, k8s_data)
elif k8s_data:
    with st.expander(f"K8s Investigation ({k8s_data.get('adapter_name', 'K8s')})", expanded=False):
        for finding in k8s_data.get("findings", []):
            st.markdown(f"- {finding}")

# Always show audit trail when K8s data exists
if k8s_data:
    with st.expander("K8s Audit Trail", expanded=False):
        _render_audit_trail(k8s_data.get("audit_trail", []))
```

- [x] **Step 3: Verify the app loads without errors**

Run: `python -c "import sentinel.interfaces.chat.app; print('OK')"`
Expected: `OK`

- [x] **Step 4: Commit**

```bash
git add src/sentinel/interfaces/chat/app.py
git commit -m "feat: add side-by-side comparison UI to Streamlit chat app"
```

---

## Task 12: Full Verification

**Files:** None (verification only)

- [x] **Step 1: Run full unit test suite**

Run: `just test`
Expected: All tests PASS.

- [x] **Step 2: Run linter**

Run: `just lint`
Expected: No errors from ruff, mypy, or import-linter.

- [x] **Step 3: Fix any lint issues**

If mypy or ruff report issues, fix them and re-run.

- [x] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve lint issues from comparison framework"
```

- [x] **Step 5: Update plan checkboxes**

In `docs/plans/k8s-agent-and-mcp-integration.md`, check off completed steps:
- [x] Step 26: Implement comparison mode in pipeline node
- [x] Step 27: Create golden cases (3 cases in JSON)
- [x] Step 28: Create comparison evaluators
- [x] Step 39: Implement audit trail viewer
- [x] Step 40: Implement comparison mode UI

Steps 29 (extend `evals/reporting.py` for `ComparisonReport`) and 30 (end-to-end comparison test) are deferred — the reporting infrastructure works as-is and the end-to-end test requires a running LLM.

```bash
git add docs/plans/k8s-agent-and-mcp-integration.md
git commit -m "docs: update plan checkboxes for comparison framework"
```
