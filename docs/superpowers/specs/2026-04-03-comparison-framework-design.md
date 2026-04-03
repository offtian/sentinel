# Comparison Framework + Streamlit UI

**Scope:** Phase E (Steps 26-30) + Phase G (Steps 39-40) from `docs/plans/k8s-agent-and-mcp-integration.md`

## Goal

Run two K8s investigation backends concurrently, score them with `EvaluationMetrics`, and display results side-by-side in the Streamlit app. A `MockKagentAdapter` stands in for the real kagent until cluster integration is ready.

## Components

### 1. MockKagentAdapter

**File:** `tests/factories/__init__.py`

Implements `K8sInvestigationAdapter` with configurable delay and canned findings. Returns realistic `InvestigationResult` with audit trail entries. Used in comparison mode tests and Streamlit demo.

```python
class MockKagentAdapter(K8sInvestigationAdapter):
    def __init__(self, *, findings: tuple[Finding, ...] = (), delay_ms: int = 200): ...
    async def investigate(self, *, alert, context=None) -> InvestigationResult: ...
    @property
    def is_configured(self) -> bool: ...
```

### 2. Comparison logic

**File:** `src/sentinel/domain/evaluation/comparison.py` (extend existing)

Add `from_investigation_results()` factory to `ComparisonResult`:

```python
@staticmethod
def from_investigation_results(
    *,
    baseline: InvestigationResult,
    challenger: InvestigationResult,
    case_id: str = "",
) -> ComparisonResult:
```

Derives metrics from two `InvestigationResult` objects:
- `evidence_source_count` — len(sources_queried)
- `evidence_diversity` — unique source types / total sources
- `latency_p50_ms` — duration_ms (single run, so p50 = actual)
- `factual_recall` — findings count ratio (challenger / baseline, capped at 1.0)
- `degradation_score` — 1.0 minus fraction of error audit entries
- `token_cost` — 0 (not available from InvestigationResult)
- Other metrics — 0.0 (need labelled data to compute precision, hallucination rate, brier score, robustness)

`winner_by_dimension` computed for each non-zero metric: adapter with the better value wins.

### 3. Comparison mode in pipeline

**File:** `src/sentinel/interfaces/graphs/sre_investigation.py`

When `K8S_INVESTIGATION_BACKEND=both`:
- `InvestigateWithHolmes` node runs both adapters via `asyncio.gather`
- Primary (holmes) flows through pipeline normally
- Secondary result + `ComparisonResult` stored on graph `State` (add `comparison_result: ComparisonResult | None` field)

When backend is `native` or `kagent`:
- Single adapter runs, no comparison

### 4. Golden test cases

**File:** `src/sentinel/evals/cases/k8s_investigation_cases.json`

Three cases:
1. **CrashLoopBackOff** — OOMKilled pod, expected keywords: "OOMKilled", "memory", "restart"
2. **Node NotReady** — kubelet heartbeat timeout, expected keywords: "NotReady", "kubelet", "heartbeat"
3. **Deployment rollout stuck** — new ReplicaSet not scaling, expected keywords: "rollout", "ReplicaSet", "scaling"

Each case: alert input, expected_keywords, min_findings_count, max_latency_ms.

### 5. Comparison evaluator

**File:** `src/sentinel/evals/evaluators/comparison_evaluator.py`

Evaluates K8s investigation results:
- **FindingsKeywordCoverage** — fraction of expected keywords found across all finding summaries
- **MinimumSourceCount** — at least N sources queried
- **LatencyThreshold** — duration_ms below threshold

Extends `pydantic_evals.evaluators.Evaluator`, same pattern as existing `KeywordCoverage` and `StructuralCheck`.

### 6. Streamlit audit trail viewer

**File:** `src/sentinel/interfaces/chat/app.py`

Expandable section below investigation results:
- Timeline rendering: each `AuditEntry` as a row with timestamp, tool name, status badge (green/red/yellow via st.markdown), duration in ms
- Freeform payload as collapsible JSON (st.expander + st.json)

### 7. Streamlit comparison mode UI

**File:** `src/sentinel/interfaces/chat/app.py`

When "Both (comparison)" selected:
- Two `st.column` side by side
- Each column: adapter name header, duration, confidence (if available), findings list, audit trail (reuse viewer from #6)
- Summary row below: which was faster, which had more findings

### 8. Persistence — comparison runs and eval results

**New DB tables** (Alembic migration `002_create_comparison_and_eval_tables.py`):

**`comparison_runs`** — stores each comparison execution:
- `id` (UUID, PK)
- `investigation_record_id` (UUID, FK to `investigation_records`, indexed)
- `baseline_adapter` (String) — e.g. "holmes"
- `challenger_adapter` (String) — e.g. "native_k8s"
- `baseline_result_json` (JSONB) — serialised `InvestigationResult`
- `challenger_result_json` (JSONB) — serialised `InvestigationResult`
- `comparison_result_json` (JSONB) — serialised `ComparisonResult` with metrics and winner_by_dimension
- `baseline_duration_ms` (Integer)
- `challenger_duration_ms` (Integer)
- `created_at` (DateTime with timezone)

**`eval_runs`** — stores evaluation framework runs:
- `id` (UUID, PK)
- `dataset_name` (String, indexed) — e.g. "k8s_investigation", "sre_investigation"
- `total_cases` (Integer)
- `passed_cases` (Integer)
- `failed_cases` (Integer)
- `average_score` (Float, nullable)
- `results_json` (JSONB) — full per-case results
- `run_duration_ms` (Integer)
- `created_at` (DateTime with timezone)

**Database access** uses `databases` library (not SQLAlchemy sessions):

```python
import databases
db = databases.Database(settings.db_url)
```

Raw SQL queries via `db.execute()` / `db.fetch_one()` / `db.fetch_all()`. Alembic still manages schema migrations (DDL only), but runtime read/write uses `databases`.

**Persistence module:** `src/sentinel/data/comparison.py`
- `persist_comparison_run(*, db, investigation_record_id, baseline, challenger, comparison_result) -> UUID`

**Persistence module:** `src/sentinel/data/eval_runs.py`
- `persist_eval_run(*, db, dataset_name, results, duration_ms) -> UUID`
- `fetch_eval_runs(*, db, dataset_name, limit) -> list[dict]`

Called from the pipeline (comparison mode) and eval runner respectively.

## Not changing

- Adapter hierarchy or investigation domain types
- Pipeline flow for non-comparison mode
- Kagent CRD logic (mock only)
- Existing evaluator infrastructure

## File summary

| Action | File |
|--------|------|
| Modify | `tests/factories/__init__.py` — add `MockKagentAdapter` |
| Modify | `src/sentinel/domain/evaluation/comparison.py` — add `from_investigation_results()` |
| Modify | `src/sentinel/interfaces/graphs/sre_investigation.py` — add comparison mode |
| Create | `src/sentinel/evals/cases/k8s_investigation_cases.json` — 3 golden cases |
| Create | `src/sentinel/evals/evaluators/comparison_evaluator.py` — 3 evaluators |
| Modify | `src/sentinel/interfaces/chat/app.py` — audit trail viewer + comparison UI |
| Create | `tests/unit/domain/evaluation/test_comparison_from_results.py` — tests for factory method |
| Create | `tests/unit/evals/evaluators/test_comparison_evaluator.py` — evaluator tests |
| Create | `tests/unit/interfaces/graphs/test_sre_comparison_mode.py` — comparison pipeline tests |
| Create | `src/sentinel/data/migrations/alembic/versions/002_create_comparison_and_eval_tables.py` — migration |
| Create | `src/sentinel/data/comparison.py` — `persist_comparison_run()` using `databases` |
| Create | `src/sentinel/data/eval_runs.py` — `persist_eval_run()`, `fetch_eval_runs()` using `databases` |
| Create | `tests/unit/data/test_comparison_persistence.py` — persistence tests |
| Create | `tests/unit/data/test_eval_runs_persistence.py` — persistence tests |
