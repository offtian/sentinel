# PydanticAI + LangGraph Adoption — Design

**Date:** 2026-04-26
**Status:** Approved (pending implementation plan)
**Related ADRs:** [0006 (PydanticAI confirmed)](../../adrs/0006-O10-pydanticai-langgraph.md), 0007 (LangGraph adopted — authored alongside this design)
**Related plans:** [sentinel-hedgefund-foundations](../../plans/sentinel-hedgefund-foundations.md) (F5 collapses; F6/F8 retarget to `workflows/`)

## Goal

Migrate Sentinel's orchestration layer from Pydantic Graph to LangGraph, harness-only. PydanticAI agents, the F2 envelope plumbing, vendor adapters, the data layer, and webhook routers all stay live and unchanged. The only thing replaced is the DAG glue.

The driver is to settle the orchestration-framework decision (RFC §15.14, originally deferred to month 3 / phase F5) by adopting the framework now and proving it on the smallest pipeline. LangGraph's native `interrupt()` + `PostgresSaver` machinery is purpose-built for the human-in-the-loop approval gate that Sentinel's pipelines already need; bringing it forward moves the decision out of "deferred risk" into "settled, exercised by support pipeline first."

## Scope

**This spec covers the umbrella architectural design for all three pipeline migrations, plus the support migration in detail.** SRE migration and chart migration each get their own brainstorm + design rounds when their phases activate (see Phasing).

**In scope (this design + the support migration PR):**

- New `src/sentinel/interfaces/workflows/` package; new support workflow built on LangGraph `StateGraph`
- `langgraph` + `langgraph-checkpoint-postgres` added to `pyproject.toml`
- `AsyncPostgresSaver` wired at app bootstrap; LangGraph's three checkpoint tables managed by `saver.setup()`
- F2 envelope helper for LangGraph: new `with_envelope` decorator in `interfaces/workflows/_envelope.py` that calls `envelope.to_span_attributes()` and `envelope.to_log_context()` directly (both are existing public primitives on `Envelope` from F2.1); no helper extraction needed
- New approval endpoints for support: `POST /api/support/responses/{request_id}/approve`, `/reject`, `GET /approval-status`
- Existing webhook handler (`POST /api/support/webhooks/jira`) hard-cuts to call the new graph
- Existing `interfaces/graphs/support_review.py` moved to `interfaces/graphs/_archive/`; import-linter contract guards against re-import
- ADR 0007 authored; ADR 0006 closed as `accepted`
- Foundations plan amended (F5 collapses to LiteLLM-proxy-only; F6/F8 retargeted to `workflows/`)
- Tests rewritten on the new harness; archived-code tests deleted

**Out of scope (deferred to own design rounds):**

- SRE migration to LangGraph (the next pipeline-migration phase, after F3 DB schema gap-fill)
- Chart-generation migration (the third migration phase; absorbs F2 chart-generation envelope cleanup at the same time)
- F3–F8 of the foundations plan (proceed independently per their existing schedules; F4–F8 land on `workflows/` SRE only when SRE has migrated)
- LangGraph checkpoint cleanup / TTL job (tech debt; tracked as a follow-up)
- Per-tenant or per-team workflow routing (single-profile foundations)
- Shadow-mode / dual-running infrastructure (rejected in favour of W1 hard cutover)

## Phasing

```
PR(N+1) — Support migration to LangGraph (this spec's implementation scope)
PR(N+2) — F3 DB schema gap-fill (independent; data layer only)
PR(N+3+) — SRE migration to LangGraph (own design round)
PR(N+4+) — F4 / F6 / F7 / F8 land on workflows/ SRE only
PR(final) — Chart workflow migration + envelope cleanup (own design round)
```

F3 is data-layer only and disjoint from the harness; doing it before SRE migrate means the SRE workflow inherits the canonical schema from day one. F4 (Langfuse + replay determinism) lands once, on the LangGraph SRE pipeline. F5 (orchestration framework decision) collapses to LiteLLM proxy migration only — the framework decision is now in ADR 0007.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Adoption strategy | Greenfield rebuild, **boundary 1** — orchestration glue only | Preserves F2 envelope work, PydanticAI agent factories, and `_node_helpers` primitives that were intentionally framework-agnostic. Throwing them away contradicts ADR 0006's "5 days harness-only" estimate. |
| Location | `src/sentinel/interfaces/workflows/` for new code; `src/sentinel/interfaces/graphs/_archive/` for archived legacy | "Workflows" reads as a deliberate framework choice (not a version bump). `_archive/` subfolder + import-linter contract mechanically prevents accidental backsliding. |
| Migration order | Support → SRE → Chart | Support is the smallest pipeline and proves LangGraph patterns on the lowest-blast-radius surface. F3 (DB schema) lands between support and SRE so SRE inherits the canonical schema. Chart last because it absorbs F2 chart-generation envelope cleanup as part of its migration. |
| State shape | TypedDict at the LangGraph boundary; `Envelope`, `Finding`, `ConfidenceScore` etc. keep their existing `attrs.frozen`/Pydantic types inside | Matches LangGraph's idiomatic state pattern (reducers, checkpointer, `Annotated[..., reducer]`) without re-typing domain primitives. F2 envelope work preserved end-to-end. |
| LangGraph idioms | `AsyncPostgresSaver` + `interrupt()` from the support migration onward | Pays the integration cost once, on the smallest pipeline; SRE migration inherits a settled pattern. Native `interrupt()` is the right primitive for the approval gate Sentinel already needs. |
| Foundations interleave | Support migrate → F3 → SRE migrate → F4–F8 on `workflows/` only | F3 is data-layer-only and disjoint from harness work. F4–F8 add nodes to the SRE pipeline; they're written once, on LangGraph, never on Pydantic Graph. |
| Webhook cutover | Hard cutover at PR merge (W1) for support; SRE will likely use a feature flag (W2) when its design lands | Support is shadowed by human review — blast radius is "Jira draft is wrong," which is harmless. SRE auto-investigates production alerts; that earns the safety belt. |
| Schema ownership | LangGraph's three tables (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) managed by `saver.setup()`, not Alembic | Library upgrades add columns; Alembic-tracked schema would fight upgrades. Trade-off recorded in ADR 0007. |
| Persistence stores | Three coexist: app schema (audit) / LangGraph checkpointer (resume state) / replay bundle (deterministic re-execution) | Each store has a different purpose; subsuming any of them into another would conflate audit, runtime-resume, and replay-determinism concerns. |
| ADR ownership | ADR 0007 authored in this PR (not deferred to F5) | The orchestration-framework decision is being made now; the ADR records it now. F5 in the foundations plan collapses to LiteLLM-proxy-only. |
| Test strategy for archived code | Delete archived-code tests; do not move to `_archive/` | Archived code is reference material only. Running CI against it gives false signal of "the old code still works" while we've stopped maintaining it. |

## Architecture

### Directory layout (after support-migration PR)

```
src/sentinel/interfaces/
├── graphs/                                # legacy harness, frozen
│   ├── _archive/
│   │   ├── __init__.py                    # marker; no re-exports
│   │   └── support_review.py              # MOVED — no longer imported from outside _archive
│   ├── sre_investigation.py               # untouched (still serving traffic)
│   ├── chart_generation.py                # untouched
│   ├── agents/                            # PydanticAI agent factories — STAY HERE
│   │   ├── ticket_reviewer.py             # used by both legacy SRE and new workflows/
│   │   └── response_drafter.py
│   └── _node_helpers.py                   # untouched — legacy SRE/chart graphs keep using it
└── workflows/                             # NEW
    ├── __init__.py
    ├── _envelope.py                       # NEW — with_envelope decorator (LangGraph variant of run_node_with_envelope)
    ├── _checkpointer.py                   # NEW — AsyncPostgresSaver builder
    ├── support_review.py                  # NEW — LangGraph StateGraph + node functions
    └── support_state.py                   # NEW — SupportReviewState TypedDict
```

### Import-linter contract additions (`pyproject.toml`)

- New contract: nothing in `src/sentinel/**` may import from `sentinel.interfaces.graphs._archive` (mechanical guard against accidental re-use).
- New contract: `sentinel.interfaces.workflows` may import from `sentinel.interfaces.graphs.agents`, `sentinel.data`, `sentinel.domain`, but **not** from `sentinel.interfaces.graphs.{sre_investigation,chart_generation}` — no cross-harness coupling.
- Existing layered contracts (interfaces > application > domain > data) unchanged.

### State (TypedDict + reused primitives)

`interfaces/workflows/support_state.py`:

```python
from typing import TypedDict
from sentinel.data.primitives.envelope import Envelope
from sentinel.domain.support import (
    Ticket, TicketClassification, DocSearchResult, ResponseSuggestion,
)
from sentinel.domain.confidence import ConfidenceScore
from sentinel.domain.approval import ApprovalDecision

class SupportReviewState(TypedDict):
    envelope: Envelope                                  # required at entry
    ticket: Ticket                                      # required at entry
    classification: TicketClassification | None         # written by classify_ticket
    doc_results: tuple[DocSearchResult, ...]            # written by search_documentation
    response_suggestion: ResponseSuggestion | None      # written by draft_response
    confidence: ConfidenceScore | None                  # written by determine_confidence
    needs_approval: bool                                # routing flag
    approval_decision: ApprovalDecision | None          # written by wait_for_human (resume payload)
```

Single-writer per field at this stage — no `Annotated[..., reducer]` needed. Reducers can be introduced later if a node accumulates.

### Node pattern + F2 helper adaptation

`interfaces/workflows/_envelope.py`:

```python
from collections.abc import Awaitable, Callable
import functools
import structlog
from sentinel.data.primitives import envelope as envelope_module

WorkflowNode = Callable[[dict], Awaitable[dict]]

def with_envelope(node_fn: WorkflowNode) -> WorkflowNode:
    """LangGraph counterpart to run_node_with_envelope."""
    @functools.wraps(node_fn)
    async def wrapped(state: dict) -> dict:
        env = state["envelope"]
        otel_trace.get_current_span().set_attributes(env.to_span_attributes())
        with structlog.contextvars.bound_contextvars(**env.to_log_context()):
            return await node_fn(state)
    return wrapped
```

The decorator calls `envelope.to_span_attributes()` and `envelope.to_log_context()` directly — both are existing public primitives on `Envelope` (F2.1). The legacy `_node_helpers.instrumented_node_run` calls the same primitives the same way. No extraction needed.

Each node is an async function returning a partial dict; ~10–30 lines:

```python
# interfaces/workflows/support_review.py
async def classify_ticket(state: SupportReviewState) -> dict:
    config = get_config()
    agent = config.agent_for("ticket_reviewer")
    result = await agent.run(_render_prompt(state["ticket"]), model=config.ticket_reviewer_llm)
    return {"classification": result.output}
```

`NodeError` / `PipelineNodeFailed` from `domain/pipeline/errors.py` continue to be raised inside nodes — those types are framework-agnostic.

### Graph builder

```python
# interfaces/workflows/support_review.py
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

def _route_after_confidence(state: SupportReviewState) -> str:
    return "wait_for_human" if state["needs_approval"] else END

@with_envelope
async def wait_for_human(state: SupportReviewState) -> dict:
    decision: ApprovalDecision = interrupt({
        "action": "approve_response_suggestion",
        "request_id": str(state["envelope"].request_id),
        "suggestion": state["response_suggestion"],
        "confidence": state["confidence"],
    })
    return {"approval_decision": decision}

def build_support_review_graph(checkpointer):
    g = StateGraph(SupportReviewState)
    g.add_node("classify_ticket", with_envelope(classify_ticket))
    g.add_node("search_documentation", with_envelope(search_documentation))
    g.add_node("draft_response", with_envelope(draft_response))
    g.add_node("determine_confidence", with_envelope(determine_confidence))
    g.add_node("wait_for_human", wait_for_human)
    g.add_edge(START, "classify_ticket")
    g.add_edge("classify_ticket", "search_documentation")
    g.add_edge("search_documentation", "draft_response")
    g.add_edge("draft_response", "determine_confidence")
    g.add_conditional_edges("determine_confidence", _route_after_confidence)
    g.add_edge("wait_for_human", END)
    return g.compile(checkpointer=checkpointer)
```

### Checkpointer

`interfaces/workflows/_checkpointer.py`:

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def build_checkpointer(settings) -> AsyncPostgresSaver:
    # Illustrative — exact API form (context-manager vs aenter) verified during implementation
    # against the langgraph-checkpoint-postgres pin. Falls back to the app's existing DSN
    # when LANGGRAPH_CHECKPOINT_DSN is unset.
    dsn = settings.langgraph_checkpoint_dsn or settings.database_url
    saver = await AsyncPostgresSaver.from_conn_string(dsn).__aenter__()
    await saver.setup()  # idempotent per library docs — creates the three checkpoint tables if absent
    return saver
```

- **Schema ownership:** LangGraph's three tables managed by `saver.setup()`, not Alembic. Recorded in ADR 0007 as a deliberate deviation from the project's "all schema in Alembic" convention. The driver is library-upgrade compatibility (LangGraph adds columns across versions).
- **Connection pool:** separate pool from the app's SQLAlchemy pool; both point at the same database. Foundations-stage tradeoff.
- **`thread_id`** = `str(envelope.request_id)`. One workflow run per request; resume keyed by request_id.

### Bootstrap sequence

`interfaces/api/app.py` lifespan:

```python
checkpointer = await build_checkpointer(settings)
app.state.support_review_graph = build_support_review_graph(checkpointer)
# (later: app.state.sre_investigation_graph for SRE migration)
```

Compiled graph built once, reused across requests. Webhook handler reads `request.app.state.support_review_graph`.

## API surface changes

### Existing endpoints (behaviour)

```
POST /api/support/webhooks/jira
   → graph.ainvoke({envelope, ticket}, config={thread_id: request_id})
   → if confidence ≥ threshold:           graph runs to END
   → if confidence < threshold:           interrupt() pauses; returns state + __interrupt__ payload
   → ResponseSuggestionRecord persisted in either case (existing audit row)
   → 200 OK { "request_id": ..., "suggestion_id": ..., "needs_approval": bool, "interrupt_payload": {...} | null }
```

### New endpoints (mirror SRE shape)

```
POST /api/support/responses/{request_id}/approve
   body: { "approver": "...", "edits": "...optional..." }
   → graph.ainvoke(Command(resume={"approved": true, ...}), config={thread_id: request_id})
   → 200 OK { "request_id": ..., "status": "approved", ... }

POST /api/support/responses/{request_id}/reject
   body: { "approver": "...", "reason": "..." }
   → graph.ainvoke(Command(resume={"approved": false, ...}), config={thread_id: request_id})
   → 200 OK { "request_id": ..., "status": "rejected", ... }

GET  /api/support/responses/{request_id}/approval-status
   → reads thread state from checkpointer; returns {"status": "pending|approved|rejected", ...}
```

Endpoints mirror the SRE approval routes (`/api/sre/investigations/{id}/approve` etc.) so the API surface converges as SRE migrates later.

## Persistence model

After this PR, a `request_id` has rows in **three stores simultaneously**:

| Store | Purpose | Source of truth for |
|---|---|---|
| App schema (`response_suggestion`, etc.) | Canonical audit | Reporting, UI, billing |
| LangGraph checkpointer (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) | Runtime resume state | "Where did this workflow pause?" |
| Replay bundle (PR #15, extended in F4) | Deterministic re-execution | Bit-for-bit replay against recorded I/O |

Three concerns, three stores. None subsumes the others.

## Error handling

- Inside nodes: `NodeError` / `PipelineNodeFailed` raised as today. LangGraph runtime catches the exception and persists it in the checkpoint as a failed step.
- Webhook returns 500 with `{"error": "pipeline_failed", "request_id": ..., "node": "..."}`.
- `EnvelopeIngressError` (F2.4) at webhook entry: unchanged. 422 response shape preserved (the error is raised before `graph.ainvoke` is called).

## Tests

```
tests/
├── unit/interfaces/workflows/                        # NEW directory
│   ├── test_envelope_decorator.py                    # NEW — with_envelope wraps span attrs + log context
│   ├── test_support_routing.py                       # NEW — _route_after_confidence cases
│   └── test_support_state.py                         # NEW — TypedDict shape, defaults
├── integration/interfaces/workflows/                 # NEW directory
│   ├── test_support_review_workflow.py               # NEW — graph.ainvoke through to END
│   ├── test_support_interrupt_resume.py              # NEW — interrupt() + Command(resume=...) round-trip
│   └── test_checkpointer_setup.py                    # NEW — saver.setup() idempotent; tables present
├── functional/
│   ├── test_support_review_workflow.py               # NEW — E2E with monkeypatched agents
│   └── test_support_review.py                        # DELETED — covered by workflow variant
└── integration/interfaces/graphs/test_support_*.py   # DELETED — code moved to _archive/, tests don't follow
```

**UNCHANGED — these stay live and support the new harness:**

- `tests/unit/test_envelope.py`
- `tests/integration/interfaces/api/test_request_id_propagation.py`
- `tests/factories/__init__.py` — `make_ticket()`, `make_response_suggestion()` reused
- `tests/functional/conftest.py` — PydanticAI monkeypatch fixture reused

**Why archived-code tests are deleted, not moved:** archived code is reference material only. Running CI against it gives false signal of "the old code still works" while we've stopped maintaining it. Signal lives where the new tests run.

**New checkpointer fixture** (`tests/integration/conftest.py`):

```python
@pytest.fixture
async def checkpointer_for_test(integration_db_url):
    saver = await AsyncPostgresSaver.from_conn_string(integration_db_url).__aenter__()
    await saver.setup()
    yield saver
    # teardown: drop checkpoint tables (or rely on test transaction scope, matching existing convention)
```

Functional test shape (with mandatory GWT comments per `.claude/rules/testing.md`):

```python
async def test_low_confidence_interrupt_then_approve(...):
    # GIVEN canned agent outputs producing confidence below threshold
    # WHEN webhook invokes graph
    # THEN graph returns with __interrupt__ payload, response shows needs_approval=True
    # WHEN approval endpoint POSTs Command(resume={"approved": True}) for the same request_id
    # THEN graph resumes, runs to END, approval_decision is recorded
    ...
```

## Dependencies

`pyproject.toml`:

```toml
[project]
dependencies = [
    # existing...
    "langgraph>=0.4.0,<0.5",                          # NEW — pin to current minor
    "langgraph-checkpoint-postgres>=2.0,<3",          # NEW — Postgres saver
]
```

Versions to be verified against the latest stable line at PR time. `uv lock` runs in the same PR. No other dep changes — `pydantic-ai`, `litellm`, `structlog`, `attrs` all stay.

## Documentation deltas (within the support-migration PR)

- `docs/architecture.md` — Pipelines section: "LangGraph StateGraphs (support) + Pydantic Graph DAGs (SRE, chart — pending migration)"; add a "Workflow harness migration" subsection cross-linking ADR 0007.
- `CLAUDE.md` — Pipelines (gotchas) section: note that support uses LangGraph idioms (`StateGraph`, `interrupt()`, `PostgresSaver`) while SRE/chart still use Pydantic Graph; update import paths.
- `AGENTS.md` — Quick Reference line on "the support pipeline lives in `interfaces/workflows/`, SRE/chart still in `interfaces/graphs/`".
- `README.md` — Tech Stack: replace "Pydantic Graph" with "LangGraph (support) + Pydantic Graph (SRE/chart, migrating)".
- `.env.default` — add `LANGGRAPH_CHECKPOINT_DSN` (commented, defaults to app DB).
- New ADR `docs/adrs/0007-orchestration-framework-langgraph.md` (status `accepted`).
- ADR 0006 closed: status moves from `proposed` to `accepted`; "Decision" + "Consequences" + "Validation" sections filled in.

## Foundations plan amendments

**`docs/plans/INDEX.md`:**

- Add row under **In Progress**: `pydanticai-langgraph-adoption | Migrate from Pydantic Graph to LangGraph harness, support-first | 1/3 phases (support next)`.
- Update `sentinel-hedgefund-foundations` row: progress note becomes "F1 + F2 complete; F5 collapsed (orchestration in ADR 0007); F3 next; F6/F8 will target workflows/".

**`docs/plans/sentinel-hedgefund-foundations.md`:**

- Phase F5 amended: drop ADR 0007 step (already authored in adoption plan); keep LiteLLM proxy migration steps F5.2–F5.7 unchanged.
- Phase F6 amended: `MatchRunbook` node added to `interfaces/workflows/sre_investigation.py` (when SRE workflow exists), not `interfaces/graphs/`.
- Phase F8 amended: `AssessQuality` node likewise targets `workflows/`.
- Out-of-scope list: drop "LangGraph migration" line.
- "Changes" table: new row dated 2026-04-26 documenting the pull-forward.

**New plan file** `docs/plans/pydanticai-langgraph-adoption.md` (using `docs/plans/_template.md`):

- Goal, scope, design decisions table mirroring this spec
- Phasing: support / SRE / chart, with the support phase fully detailed and SRE/chart phases as placeholders awaiting their own brainstorm rounds
- Steps for the support migration phase (TDD-shaped: tests first, implementation, archive move, doc updates)

## Risks & open questions

| Risk | Mitigation |
|---|---|
| LangGraph 0.4 API churn before our pin window expires | Pin tight (`>=0.4.0,<0.5`); revisit on 0.5 release with a dedicated upgrade PR |
| Checkpointer tables collide with future LangGraph schema migrations | `saver.setup()` is idempotent; library upgrade PRs include schema-delta verification step |
| `AsyncPostgresSaver` connection pool starvation under load | Foundations-stage traffic doesn't warrant tuning; tech debt for a later observability PR |
| Approval endpoint `Command(resume=...)` semantics need investigation in `langgraph-checkpoint-postgres` ≥ 2.0 | Implementation plan's first task is a spike: instantiate the saver and run a minimal interrupt/resume round-trip in a scratch test before writing the real graph |
| Checkpoint accumulation (no cleanup job in this PR) | Captured as tech debt; 30-day default retention noted; cleanup scheduled in a follow-up plan |

## Next step

Invoke `superpowers:writing-plans` to produce the implementation plan for the support-migration PR. The plan slots into `docs/plans/pydanticai-langgraph-adoption.md` per CLAUDE.md's plan-location override.
