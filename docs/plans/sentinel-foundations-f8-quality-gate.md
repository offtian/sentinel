# Plan: F8 — Quality Gate (Deterministic Groundedness) + Replay Determinism CI

**Status:** complete
**Created:** 2026-05-02
**RFC:** §5.4 + R-QG-1 + R-AG-4 + R-CO-1
**Parent plan:** [sentinel-hedgefund-foundations.md](sentinel-hedgefund-foundations.md)

## Goal

Close the foundations loop. Deliver:
1. Deterministic groundedness gate — every `Finding` must cite at least one queried source
2. `AssessQuality` node wired into the LangGraph SRE workflow
3. Audit trail writes for key state transitions (R-CO-1)
4. Replay determinism test fixed and confirmed in CI (R-AG-4)

## LangGraph Adaptation Note

The parent F8 plan was authored before the LangGraph SRE migration completed (PR #35).
All graph modifications target `interfaces/workflows/sre_investigation.py`, NOT the
now-archived `interfaces/graphs/investigation.py`. Key differences from the original F8 spec:

- `AssessQuality` node goes into the LangGraph workflow (not the Pydantic Graph)
- F8.3 "return `End(failure_mode=...)`" → softer: set `needs_approval=True` when groundedness
  fails (routes to approval gate instead of hard-failing the investigation)
- `test_replay_determinism.py` imports the broken `interfaces.graphs.investigation` path;
  the test must be rewritten to use the new LangGraph API

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| `Finding.evidence_refs` | Add `tuple[str, ...]` field (default empty) | RFC §5.4 requires `evidence_refs`; adding to `Finding` is backward-compatible; populated from `investigation_sources` in `analyse_root_cause` |
| Groundedness check logic | Finding passes iff `evidence_refs` non-empty | Simple, deterministic; no DB FK lookup needed in foundations |
| Groundedness failure behaviour | Force `needs_approval=True`, not hard-fail | Softer approach for foundations; prevents mis-tuned gate from silently dropping investigations; human reviewer sees the low-confidence result |
| `assess_quality` position | After `analyse_root_cause`, before `determine_confidence` | Same as original F8 spec; findings are available, confidence not yet computed |
| Audit trail scope | `investigate_alert` and `resume_investigation` entry points | R-CO-1 requires transitions; entry-point writes cover received→matched→investigated→published |
| Replay test target | LangGraph workflow via `MemorySaver` + patched `get_config` | Old Pydantic Graph API is archived; LangGraph graph builder accepts any checkpointer |

## Pre-existing bug in scope

`src/sentinel/interfaces/slack/event_handlers.py:288-295` has dead code that references
`reply` (only bound in the legacy pipeline path) when the LangGraph path is active.
Fix required to get the unit test suite green before F8 work begins.

## Steps

### Step 1: Fix pre-existing `UnboundLocalError` in `event_handlers.py`

**Files changed:**
- `src/sentinel/interfaces/slack/event_handlers.py`

**What:** Lines 288-295 overwrite `blocks` with `slack_blocks.investigation_summary_blocks(reply.alert_id, ...)`
where `reply` is only set in the `else` (legacy) branch. The LangGraph path already
sets `blocks` at line 272. Remove the dead block; the `await status.replace_with_result(blocks=blocks)`
at line 296 uses the `blocks` computed by whichever branch ran.

**Verify:** `just test tests/unit/interfaces/slack/test_sre_routing.py` → green

---

### Step 2: Add `evidence_refs` to `Finding` + populate in `analyse_root_cause`

**Files changed:**
- `src/sentinel/domain/investigations/entities.py`
- `src/sentinel/interfaces/workflows/sre_investigation.py` (populate `evidence_refs`)

**What:**

`entities.py`:
```python
class Finding(BaseModel):
    source: str
    summary: str
    raw_data: str | None = None
    relevance: float = 0.0
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)  # NEW
```

`sre_investigation.py` (`analyse_root_cause`): when building findings from
`investigation_sources`, set `evidence_refs=(source,)` so each finding cites its source:
```python
findings = [
    investigation_entities.Finding(
        source=source,
        summary=evidence,
        relevance=analysis.confidence,
        evidence_refs=(source,),  # NEW
    )
    for source, evidence in zip(investigation_sources, analysis.evidence, strict=False)
]
```

**Verify:** `just test` → green (backward-compat; existing tests that construct `Finding`
without `evidence_refs` still work — field has a default)

---

### Step 3: Create `domain/quality/groundedness.py` (TDD)

**Files created:**
- `src/sentinel/domain/quality/__init__.py`
- `src/sentinel/domain/quality/groundedness.py`
- `tests/unit/domain/quality/__init__.py`
- `tests/unit/domain/quality/test_groundedness.py`

**TDD — write tests first:**

```python
class TestAssessGroundedness:
    def test_finding_with_evidence_ref_passes(self) -> None: ...
    def test_finding_without_evidence_ref_fails(self) -> None: ...
    def test_empty_findings_vacuously_passes(self) -> None: ...
    def test_skipped_investigation_vacuously_passes(self) -> None: ...
    def test_failed_investigation_vacuously_passes(self) -> None: ...
    def test_mixed_findings_partial_fail(self) -> None: ...
    def test_groundedness_verdict_is_frozen(self) -> None: ...
```

**Implementation:**

```python
@attrs.frozen(kw_only=True)
class GroundednessVerdict:
    passed: bool
    missing_evidence_finding_indices: tuple[int, ...]
    reason: str

def assess_groundedness(
    *,
    findings: Sequence[investigation_entities.Finding],
    investigation_status: str,
) -> GroundednessVerdict:
    # Skip vacuously when no investigation ran
    if investigation_status in ("skipped", "failed"):
        return GroundednessVerdict(
            passed=True, missing_evidence_finding_indices=(), reason="no investigation performed"
        )
    # Empty findings — vacuously pass
    if not findings:
        return GroundednessVerdict(
            passed=True, missing_evidence_finding_indices=(), reason="no findings to ground"
        )
    missing = tuple(i for i, f in enumerate(findings) if not f.evidence_refs)
    if missing:
        return GroundednessVerdict(
            passed=False,
            missing_evidence_finding_indices=missing,
            reason=f"{len(missing)} finding(s) lack evidence references",
        )
    return GroundednessVerdict(
        passed=True, missing_evidence_finding_indices=(), reason="all findings grounded"
    )
```

**Verify:** `just test tests/unit/domain/quality/test_groundedness.py` → green

---

### Step 4: Add `quality_verdict` to `InvestigationState` + `assess_quality` node

**Files changed:**
- `src/sentinel/interfaces/workflows/sre_state.py`
- `src/sentinel/interfaces/workflows/sre_investigation.py`

**State addition** (`sre_state.py`):
```python
from sentinel.domain.quality import groundedness as groundedness_mod
# ...
quality_verdict: groundedness_mod.GroundednessVerdict | None
```

**New node** (`sre_investigation.py`):
```python
async def assess_quality(state: InvestigationState) -> dict[str, Any]:
    investigation = state.get("investigation")
    inv_ctx = state.get("_investigation_context", {})
    investigation_status = inv_ctx.get("status", "skipped")
    findings = tuple(investigation.findings) if investigation else ()
    verdict = groundedness_mod.assess_groundedness(
        findings=findings,
        investigation_status=investigation_status,
    )
    logs.log_event("quality_assessed", params={
        "alert_id": state["alert"].id,
        "groundedness_pass": verdict.passed,
        "reason": verdict.reason,
    })
    return {"quality_verdict": verdict}
```

**Updated `determine_confidence`**: after computing `needs_approval`, also check:
```python
quality_verdict = state.get("quality_verdict")
if quality_verdict is not None and not quality_verdict.passed:
    needs_approval = True
    logs.log_event("groundedness_check_failed", params={...})
```

**Graph builder update**: add node and edge between `analyse_root_cause` and `determine_confidence`:
```python
builder.add_node("assess_quality", cast("Any", envelope_mod.with_envelope(assess_quality)))
builder.add_edge("analyse_root_cause", "assess_quality")
builder.add_edge("assess_quality", "determine_confidence")
# Remove direct edge: "analyse_root_cause" → "determine_confidence"
```

**Verify:** `just test tests/functional/` and `just test tests/unit/interfaces/workflows/` → green

---

### Step 5: Implement `application/audit/__init__.py` (R-CO-1)

**Files changed:**
- `src/sentinel/application/audit/__init__.py`

**What:** Implement `record_transition(*, request_id, from_state, to_state, reason, db_session)`.
Writes an `AuditLogRecord` row with `action=to_state`, `resource_type="investigation"`,
`resource_id=str(request_id)`, `actor="pipeline"`, `details_json={"from": from_state, "reason": reason}`.
The WORM trigger (`prev_hash`) is computed server-side by the Postgres trigger from F3.6.

Wire calls from `investigate_alert` (transition `received→completed`) and
`resume_investigation` (transition `awaiting_approval→approved/rejected`) in
`sre_investigation.py` — soft-fail if no `db_session_factory` configured.

**Verify:** `just lint` → green; `just test tests/unit/application/audit/` → green

---

### Step 6: Fix and rewrite `test_replay_determinism.py`

**Files changed:**
- `tests/integration/test_replay_determinism.py`

**Problem:** Imports `from sentinel.interfaces.graphs import investigation` which no longer exists.
The test uses the old Pydantic Graph API.

**Rewrite to use LangGraph API:**

```python
from langgraph.checkpoint.memory import MemorySaver
from sentinel.interfaces.workflows import sre_investigation as sre_mod

async def _run_pipeline(*, alert, envelope, config_mock, recorded_toolset=None):
    graph = sre_mod.build_sre_investigation_graph(checkpointer=MemorySaver())
    with mock.patch.object(sre_mod, "get_config", return_value=config_mock):
        outcome = await sre_mod.investigate_alert(
            alert=alert, envelope=envelope, graph=graph
        )
    return outcome  # InvestigationOutcome — serialise to dict for comparison
```

The `CapturingModel` / `RecordedModel` injection pattern:
- Build config mock with agents whose `.model` is set to `CapturingModel`
- After capture, swap each agent's `.model` to `RecordedModel(bundle.llm_io)`
- For the recording step: bind `ReplayBundleBuilder` via `runtime_mod.bind_replay_builder`

**Verify:** `just test-integration -k test_replay` → green (slow marker kept)

---

### Step 7: CI confirmation (F8.6)

**Files changed:**
- `.github/workflows/ci.yml` (if needed — confirm integration job runs)

**What:** The existing CI workflow already runs `uv run pytest tests/integration/` in the
`integration` job. Confirm the step command includes the full `tests/integration/` glob
(not a subset). If the `--slow` marker is excluded, add it explicitly.

Check current CI integration step command and verify the 30-run determinism test is included.
If excluded by marker, add `--slow` to the integration job args or add a separate nightly job.

**Verify:** CI workflow file reviewed; determinism test reachable under `just test-integration`

---

### Step 8: PRD and architecture docs

**Files changed:**
- `docs/prd.md` — tick R-QG-1, R-AG-4, R-CO-1; tick F8 steps in parent hedgefund plan
- `docs/architecture.md` — add §Quality Gate section under the SRE pipeline diagram
- `docs/plans/sentinel-hedgefund-foundations.md` — mark F8 steps complete

**What:**
- R-QG-1: "gate rejects fixture with empty evidence_refs" — tested in Step 3 unit tests
- R-AG-4: "30-run determinism CI" — covered by Step 6 fixed test
- R-CO-1: "audit_log write for every state transition" — covered by Step 5

Architecture doc §Quality Gate addition:
```
assess_quality node sits between analyse_root_cause and determine_confidence.
Inputs: investigation.findings + _investigation_context["status"]
Output: GroundednessVerdict(passed, missing_evidence_finding_indices, reason)
Failure mode: forces needs_approval=True so human reviews any ungrounded finding.
No hard-fail in foundations — soft-route to approval gate instead.
```

---

## Verification Summary

After all steps:

```bash
just test                                # unit suite green (including new groundedness tests)
just test tests/functional/              # functional suite green
just test-integration -k test_replay    # replay determinism 30 runs pass
just lint                               # ruff + mypy + import-linter clean
```

## Success Criteria

- [ ] R-QG-1: `test_groundedness.py` includes a test that verifies gate rejects findings with empty `evidence_refs`
- [ ] R-AG-4: `test_replay_determinism.py` collects and runs 30 iterations against LangGraph workflow
- [ ] R-CO-1: `record_transition()` in `application/audit/__init__.py`; wired from `investigate_alert` entrypoint
- [ ] Pre-existing bug fixed: unit test suite fully green (was 1 fail before F8)
- [ ] `just lint` clean
- [ ] All docs updated and PRD checkboxes ticked
