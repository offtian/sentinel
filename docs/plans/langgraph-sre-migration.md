# Plan: LangGraph SRE Migration + Typed Observability

**Status:** draft
**Created:** 2026-04-29
**Last updated:** 2026-04-29

## Goal

Migrate the SRE investigation pipeline from Pydantic Graph
(`interfaces/graphs/investigation.py`) to LangGraph
(`interfaces/workflows/sre_investigation.py`), following the patterns the
support migration established. SRE-specific scope additions:

- **Webhook cutover via feature flag (W2)** — SRE auto-investigates production
  alerts so a flag-controlled rollback is safer than the hard-cut used for
  support.
- **Post-approval `publish_findings` node** — support ended at the approval
  gate; SRE must publish after approval, so the graph adds a routing edge
  beyond the gate.
- **Typed observability layer** at `src/sentinel/utils/observability/` —
  Pydantic span-attribute models, OTel GenAI semantic conventions, and
  PydanticAI `Usage` → token/cost extraction. Lands first, used by both
  legacy chart pipeline (still on Pydantic Graph) and the new SRE workflow,
  so the contract is proven cross-framework before chart migrates.

This plan is the **SRE sub-plan** under the umbrella
[`pydanticai-langgraph-adoption.md`](pydanticai-langgraph-adoption.md).
ADR 0007 (orchestration framework) is already accepted. Chart-coding
pipeline migration is out of scope here and gets its own follow-up plan.

## Scope

### In scope (this plan, SRE-migration PR)

- New `src/sentinel/utils/observability/` package — typed span-attribute
  wrappers, gen_ai.* semconv constants, token/cost extraction.
- New `src/sentinel/interfaces/workflows/sre_investigation.py` and
  `sre_state.py`. Reuses existing `_envelope.py` and `_checkpointer.py`.
- Port six existing SRE nodes (`classify_alert`, `match_runbook`,
  `investigate`, `analyse_root_cause`, `determine_confidence`,
  `publish_findings`) plus a new `wait_for_human` node for the approval
  gate.
- Approval gate redesigned with `interrupt()` + `Command(resume=...)`;
  resumed via the existing `POST /api/sre/investigations/{id}/approve|reject`
  endpoints (the endpoints stay; their handlers learn to detect a LangGraph
  thread and route accordingly).
- Feature flag `langgraph_sre_enabled` (W2) gates the worker routing;
  Pydantic Graph remains live until cutover.
- Legacy `interfaces/graphs/investigation.py` moves to
  `interfaces/graphs/_archive/` after cutover; archived-code tests deleted.
- Legacy `interfaces/graphs/_node_helpers.py` and
  `interfaces/graphs/agents/utils.py::set_agent_span_attributes` are
  refactored to consume the typed observability layer (additive — Sentinel-
  named attrs preserved alongside new gen_ai.* ones).
- Import-linter contracts updated; foundations plan amended to note SRE on
  LangGraph; documentation deltas across `docs/architecture.md`, `CLAUDE.md`,
  `AGENTS.md`, `README.md`, `docs/prd.md`, `docs/plans/INDEX.md`.

### Out of scope (deferred / follow-up plans)

- Chart-coding migration — own plan; absorbs F2 envelope cleanup.
- Removing Pydantic Graph entirely — happens once chart migrates.
- Migrating PydanticAI agents to LangChain — explicitly preserved.
- LangGraph subgraphs.
- LangGraph checkpoint TTL / cleanup job.
- Per-tenant or per-team workflow routing.

### Already shipped (no-op for this plan)

| Reference | Existing artefact |
|---|---|
| `langgraph` + `langgraph-checkpoint-postgres` deps | `pyproject.toml` (umbrella T2) |
| `AsyncPostgresSaver` factory | `interfaces/workflows/_checkpointer.py` |
| `with_envelope` decorator | `interfaces/workflows/_envelope.py` |
| Support workflow on LangGraph | `interfaces/workflows/support_review.py` |
| Identity envelope | `data/primitives/envelope.py` |
| RFC §13.2 mandatory-attrs validator + propagator | `utils/langfuse_export.py` |
| Langfuse OTLP wiring | `bootstrap_otel.py` |
| Confidence scoring | `domain/confidence/entities.py::ConfidenceScore.from_factors` |
| F7 RunbookGrant scoping | `plugins/toolsets/_runbook_scope.py` |
| ExecutionTracer (replay bundles) | `domain/pipeline/tracer.py` |
| SRE approval endpoints | `interfaces/api/routers/sre/...` |

## Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Cutover mechanism | Feature flag (W2) | Per umbrella plan; SRE serves production alerts so a flag-controlled rollback is required |
| Node organisation | Flat single file (`sre_investigation.py`), mirroring `support_review.py` | Established pattern; if file >800 lines after porting (likely, given 6 nodes vs support's 5), extract per-node helper modules in a follow-up |
| State shape | TypedDict mirroring `SupportReviewState` | Domain entities (`Envelope`, `Alert`, `Investigation`, `Runbook`, `RunbookMatch`) keep existing types inside |
| Approval gate | `interrupt()` in `wait_for_human` node, then `_route_after_approval` decides `publish_findings` vs `END` | Differs from support (support ends at approval); SRE must publish after approve |
| Observability typing | Pydantic models for span attributes; emits gen_ai.* semconv + Sentinel attrs + Langfuse namespace via `.to_otel_dict()` | User-requested; Langfuse parses gen_ai.* natively for Generation views; Pydantic chosen per `python.md` exception for boundary types |
| Land typed obs first | Phase 1 retrofits legacy `_node_helpers.py` and agent utils before SRE workflow exists | Proves layer on running code (legacy chart still uses these helpers); avoids two parallel attribute paths |
| Token + cost | `gen_ai.usage.input_tokens` / `output_tokens` / `total_tokens` plus `sentinel.cost_usd` from LiteLLM pricing | Currently stubbed; Langfuse populates Generation cost dashboards from these |
| Worker routing | `worker._run_sre_investigation` reads `langgraph_sre_enabled`; dispatches legacy or workflow | Single switch point; both paths instrument identically via shared observability layer |
| Approval endpoint routing | Endpoint handler detects existence of LangGraph checkpoint for `request_id`; resumes via `Command(resume=...)`; falls back to legacy approval flow when none | Same external API; internal dispatch flag-aware |
| Test strategy | TDD per node; integration with `MemorySaver`; persistence with `AsyncPostgresSaver`; parity vs legacy under flag-off shadow on staging | Mirrors umbrella support migration; parity test catches semantic drift before cutover |
| Persistence ownership | Three stores coexist (app schema / checkpointer / replay bundle) | Inherited from umbrella plan; each has a different audience |
| Schema management | `AsyncPostgresSaver.setup()` owns LangGraph tables (already in place from support migration) | LangGraph upgrades add columns; Alembic-tracked schema would fight upgrades |

## Architecture

### Directory layout (after this PR)

```
src/sentinel/
├── interfaces/
│   ├── graphs/
│   │   ├── _archive/
│   │   │   ├── support_review.py        # archived in umbrella PR(N+1)
│   │   │   └── investigation.py         # MOVED here in this PR (post-cutover)
│   │   ├── chart_generation.py          # untouched (own migration plan later)
│   │   ├── common.py                    # untouched
│   │   ├── _node_helpers.py             # refactored to consume utils/observability
│   │   └── agents/                      # untouched (shared with workflows/)
│   │       └── utils.py                 # set_agent_span_attributes uses AgentSpanAttributes
│   └── workflows/
│       ├── _envelope.py                 # reused
│       ├── _checkpointer.py             # reused
│       ├── support_review.py            # reused
│       ├── support_state.py             # reused
│       ├── sre_investigation.py         # NEW — graph + nodes + entrypoints
│       └── sre_state.py                 # NEW — InvestigationState TypedDict
└── utils/
    └── observability/                   # NEW
        ├── __init__.py                  # re-exports the four *Attributes classes
        ├── spans.py                     # NodeSpanAttributes, AgentSpanAttributes, ToolSpanAttributes, UsageAttributes
        ├── semconv.py                   # gen_ai.* constant names (insulated from incubating namespace)
        └── usage.py                     # extract_usage(pydantic_ai_usage, model_name) -> UsageAttributes
```

### Graph shape

```
START
  → classify_alert
  → match_runbook
  → investigate
  → analyse_root_cause
  → determine_confidence
  → conditional: needs_approval ? wait_for_human : publish_findings
                 wait_for_human → conditional: approval_decision == APPROVED ? publish_findings : END
                 publish_findings → END
```

### Persistence model (unchanged from umbrella)

| Store | Role |
|---|---|
| App schema (`investigation`, `confidence_score`, `finding`, `response_suggestion`) | Canonical audit |
| LangGraph checkpointer (`langgraph` schema) | Resume state for paused investigations |
| Replay bundle (F4 Phase B) | Deterministic re-execution |

### Settings additions

- `langgraph_sre_enabled: bool = False` — feature flag W2.
- Surfaced on `BaseConfiguration` via `@property`.

### Typed observability — span-attribute contract

`utils/observability/spans.py` exposes four Pydantic models, all
`frozen=True`. Each has a `.to_otel_dict() -> dict[str, otel_types.AttributeValue]`
method that produces a flat dict suitable for `span.set_attributes(...)`.

| Model | Purpose | Keys (selected) |
|---|---|---|
| `NodeSpanAttributes` | RFC §13.2 mandatory + node-local + Langfuse | `request_id`, `tenant_id`, `cluster_id`, `region`, `pii_class`, `received_at`, `team_profile`, `pipeline`, `node`, `langfuse.session.id`, `langfuse.user.id`, `langfuse.observation.type` |
| `AgentSpanAttributes` | OTel GenAI conv + Sentinel agent context | `gen_ai.system`, `gen_ai.request.model`, `gen_ai.operation.name`, `prompt_version_sha`, `model_id`, `team_profile` |
| `ToolSpanAttributes` | Tool invocation + F7 grant | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `runbook_grant_id` (optional) |
| `UsageAttributes` | Token + cost from PydanticAI Usage | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.total_tokens`, `sentinel.cost_usd` |

`MandatoryAttributesValidator` and `MandatoryAttributesPropagator` in
`utils/langfuse_export.py` are unchanged — they read attribute *names* off
spans regardless of how those names got there.

## Steps

Each step is a small TDD slice or focused commit. Phases sequenced so each
phase ends in a green CI state.

### Phase 1 — Typed observability foundation

- [x] **T1** TDD `utils/observability/spans.py` — `NodeSpanAttributes` Pydantic model, `.to_otel_dict()` returns RFC §13.2 attrs + `team_profile` + `pipeline`/`node` + `langfuse.*` keys
- [x] **T2** TDD `AgentSpanAttributes` — gen_ai.system / request.model / operation.name + `prompt_version_sha` + `model_id` + `team_profile`
- [x] **T3** TDD `ToolSpanAttributes` — gen_ai.tool.name / call.id + optional `runbook_grant_id`
- [x] **T4** TDD `UsageAttributes` — gen_ai.usage.input_tokens / output_tokens / total_tokens + `sentinel.cost_usd`
- [x] **T5** TDD `utils/observability/semconv.py` — re-export OTel incubating gen_ai constants under stable Sentinel-owned names so import sites are insulated from namespace churn
- [x] **T6** TDD `utils/observability/usage.py` — `extract_usage(pydantic_ai_usage, *, model_name) -> UsageAttributes` reading `request_tokens`/`response_tokens`/`total_tokens` and looking up cost via `litellm.cost_calculator.completion_cost`
- [x] **T7** Refactor `interfaces/graphs/_node_helpers.py` — `instrumented_node_run` and `run_pipeline_with_envelope` set attributes via `NodeSpanAttributes(...).to_otel_dict()` (additive: existing keys preserved, gen_ai.* / Langfuse keys added)
- [x] **T8** Refactor `interfaces/graphs/agents/utils.py::set_agent_span_attributes` — consumes `AgentSpanAttributes`; emit gen_ai.* alongside `prompt_version_sha`/`model_id`; adds `stamp_usage_attributes` helper
- [x] **T9** Wire `extract_usage(...)` at `Investigate` and `AnalyseRootCause` agent.run sites in legacy `investigation.py` (highest-token nodes; proves layer on running code)
- [x] **T10** Wire `extract_usage(...)` at the support workflow `classify_ticket` and `draft_response` agent.run sites
- [x] **T11** Extend `tests/unit/utils/test_langfuse_export.py` — assert gen_ai.* attrs land alongside RFC §13.2 attrs on agent and node spans
- [x] **T12** TDD `tests/unit/utils/observability/` — happy-path + missing-field tests for each `*Attributes` class
- [x] **T13** Phase 1 commit; `just lint` + `just test` green (integration skipped — no DB in local env)

### Phase 2 — SRE LangGraph workflow scaffolding

- [x] **T14** Add `langgraph_sre_enabled: bool = True` to `Settings` (`.env.default` row); surface as `@property` on `BaseConfiguration`
- [x] **T15** TDD `interfaces/workflows/sre_state.py` — `InvestigationState` TypedDict with `envelope`, `alert`, `classification_category`, `runbook`, `runbook_match`, `runbook_match_id`, `requires_approval`, `investigation`, `confidence`, `needs_approval`, `approval_decision`, `findings_published`
- [x] **T16** Scaffold `interfaces/workflows/sre_investigation.py` — module docstring, imports, module-local aliases (`get_config`, `interrupt`)

### Phase 3 — Port SRE nodes (TDD per node)

- [x] **T17** TDD `classify_alert` — invokes alert_classifier agent, structured logging, returns `{"classification_category": ..., "alert": updated_alert}`
- [x] **T18** TDD `match_runbook` — runbook tag match → disambiguator on tie / RAG fallback; returns `{"runbook": ..., "runbook_match": ..., "runbook_match_id": ..., "requires_approval": ...}`
- [x] **T19** TDD `investigate` — investigator agent + K8s adapter + optional challenger comparison; returns `{"investigation": Investigation(analysis=..., sources=..., tool_calls=...)}`
- [x] **T20** TDD `analyse_root_cause` — root_cause_analyser agent with runbook skill injection; returns `{"investigation": investigation.with_root_cause(...)}`
- [x] **T21** TDD `determine_confidence` — `ConfidenceScore.from_factors(...)`; returns `{"confidence": ..., "needs_approval": confidence.total < threshold}`
- [x] **T22** TDD `wait_for_human` — `interrupt({"action": "approve_investigation", "request_id": ..., "summary": ..., "root_cause": ..., "remediation": ..., "confidence_total": ..., "confidence_label": ...})`; resume payload mapped to `ApprovalDecision`
- [x] **T23** TDD `publish_findings` — Slack post + PagerDuty update; gated on `approval_decision == APPROVED` when `needs_approval`, unconditional otherwise; returns `{"findings_published": True}`

### Phase 4 — Wire graph + entrypoints

- [x] **T24** TDD `_route_after_confidence` — branches to `wait_for_human` if `needs_approval` else `publish_findings`; `path_map` enumerates both targets
- [x] **T25** TDD `_route_after_approval` — branches to `publish_findings` if `approval_decision == APPROVED` else `END`; `path_map` enumerates both
- [x] **T26** TDD `build_sre_investigation_graph(*, checkpointer)` — composes StateGraph; every node wrapped in `with_envelope`
- [x] **T27** TDD `InvestigationOutcome` (attrs.frozen) — mirrors support's `ReviewOutcome` with SRE-specific fields (root_cause, remediation, findings_published)
- [x] **T28** TDD `investigate_alert(*, alert, envelope, graph) -> InvestigationOutcome` — seeds state, calls `graph.ainvoke`, maps state to outcome
- [x] **T29** TDD `resume_investigation(*, request_id, decision, graph, approver, reason)` — `Command(resume={"approved": ..., "approver": ..., "reason": ...})`
- [x] **T30** TDD `get_investigation_status(*, request_id, graph) -> InvestigationStatus | None` — reads checkpoint state, classifies as pending/approved/rejected/completed

### Phase 5 — Lifespan + worker + endpoint wiring

- [ ] **T31** Extend FastAPI lifespan to build SRE graph alongside support graph; expose on `app.state.sre_investigation_graph`
- [ ] **T32** TDD update `worker._run_sre_investigation` — reads `langgraph_sre_enabled`; routes to `workflows.sre_investigation.investigate_alert` when true, legacy when false; both paths emit identical telemetry via shared observability layer
- [ ] **T33** Update `interfaces/api/routers/sre/...` approval/reject/status endpoints — detect existence of LangGraph checkpoint for `request_id`; route to `resume_investigation` / `get_investigation_status` when present, legacy approval flow when absent
- [ ] **T34** Update `interfaces/slack/event_handlers.py` approval-decision branch — same flag-aware routing
- [ ] **T35** TDD update for the manual-trigger endpoint `POST /api/sre/investigate` — same flag-aware dispatch
- [ ] **T36** TDD update for `interfaces/chat/app.py` and `replay.py` callers of legacy `investigate_alert` — same flag-aware dispatch (or a thin shim if the chat surface is read-only)

### Phase 6 — Integration + parity tests

- [ ] **T37** Integration test `tests/integration/test_sre_workflow_happy_path.py` — webhook → investigation → high confidence → publish_findings → END (with `MemorySaver`)
- [ ] **T38** Integration test `tests/integration/test_sre_workflow_interrupt.py` — webhook → low confidence → interrupt → approve endpoint → resume → publish_findings → END (with `AsyncPostgresSaver` against test DB)
- [ ] **T39** Integration test `tests/integration/test_sre_workflow_reject.py` — same as above but rejected; assert publish_findings does NOT run; assert audit log row recorded
- [ ] **T40** Persistence test `tests/integration/test_sre_workflow_crash_recovery.py` — kill mid-run, restart, resume from checkpoint
- [ ] **T41** Parity test `tests/integration/test_sre_legacy_vs_workflow_parity.py` — same input on both impls (flag on for one, off for other), assert `InvestigationReply` core fields equivalent (classification_category, root_cause, remediation summary, confidence label, requires_approval)
- [ ] **T42** Span contract tests `tests/integration/test_sre_workflow_spans.py` — assert gen_ai.*, langfuse.session.id, RFC §13.2 mandatory attrs all present on workflow runs
- [ ] **T43** Replay-bundle test — assert ExecutionTracer captures the workflow run identically to legacy (same bundle shape; replay re-executes deterministically)

### Phase 7 — Cutover + cleanup (separate commit cluster)

- [ ] **T44** Enable `langgraph_sre_enabled=true` in staging; soak ≥7 days; monitor `sentinel.alert.investigation` Langfuse Sessions for divergence
- [ ] **T45** Cutover: enable `langgraph_sre_enabled=true` in production; confirm Langfuse Generation views populate token/cost; both legacy and workflow traces visible during overlap window
- [ ] **T46** After 14 days canonical=true with no rollback events: move `interfaces/graphs/investigation.py` → `interfaces/graphs/_archive/investigation.py`
- [ ] **T47** Delete legacy SRE tests under `tests/unit/interfaces/graphs/test_investigation*.py` and `tests/integration/test_sre_pipeline*.py` (archived code is reference-only)
- [ ] **T48** Update import-linter contracts in `pyproject.toml` — forbid imports from `interfaces/graphs/_archive/`; permit `interfaces/workflows/sre_investigation` from `worker`, lifespan, endpoints
- [ ] **T49** Update `worker._run_sre_investigation` — remove flag branch, call workflow directly
- [ ] **T50** Update `interfaces/api/routers/sre/...` and `interfaces/slack/event_handlers.py` — remove legacy approval-flow branch
- [ ] **T51** Update `docs/architecture.md` §Pipelines — describe workflow-based SRE; link to ADR 0007
- [ ] **T52** Update `CLAUDE.md` — `interfaces/workflows/` is the canonical home for new pipeline code; chart still on `interfaces/graphs/`
- [ ] **T53** Update `AGENTS.md` — add `interfaces/workflows/` import patterns and the `with_envelope` requirement
- [ ] **T54** Update `README.md` — pipeline table notes "SRE on LangGraph" alongside support; environment-variable table adds `LANGGRAPH_SRE_ENABLED`
- [ ] **T55** Update `docs/plans/INDEX.md` — mark this plan complete; reference chart migration as next sub-plan
- [ ] **T56** Update `docs/prd.md` acceptance criteria — tick "SRE pipeline runs on LangGraph"
- [ ] **T57** Update `docs/plans/sentinel-hedgefund-foundations.md` if any phase row references SRE-on-pydantic-graph (audit at this step)
- [ ] **T58** Final QA: `just lint`, `just test`, `just test-integration`, `just test-evals`, `just docker-compose-up` smoke; commit the cleanup PR

## Risks and Mitigations

- **Reducer semantics surprise.** LangGraph merges TypedDict updates per
  key — parallel writes need `Annotated[..., reducer]`. Today's pipelines
  don't fan out; the seam exists in `sre_state.py` for future use.
  *Mitigation*: lint check in T15 flags any new `list[...]` field on the
  state without a reducer annotation.
- **Logfire PII scrubber redacts `langfuse.session.id`-looking values.**
  *Mitigation*: pass `scrubbing_callback` to `logfire.configure` exempting
  the `langfuse.*` namespace; T11 includes a span-attribute survival test.
- **Token/cost double-counting.** PydanticAI may emit usage on parent and
  child spans; LiteLLM's pricing path may also emit separately.
  *Mitigation*: `extract_usage(...)` only stamps on the agent span (one
  level above the model span); add `use_aggregated_usage_attribute_names=True`
  on PydanticAI's instrumentation if Langfuse Generation cost shows
  inflation during T44.
- **F7 RunbookGrant + interrupt() interaction.** When the graph resumes,
  `RuntimeContext` is reconstructed — agent deps including
  `_tool_call_counters` start fresh. Counters were per-run by design;
  resume = same run logically, fresh counters is correct.
  *Mitigation*: documented in `wait_for_human` node docstring; T22 test
  asserts counters reset on resume and grants still enforced.
- **Approval endpoint dual-routing transition window.** During cutover,
  some investigations are pre-flag-flip (legacy thread, no checkpoint),
  some post-flag-flip (LangGraph thread, checkpoint exists). Endpoint
  must handle both.
  *Mitigation*: T33 routing is presence-based (checkpoint exists?), not
  flag-based — works correctly regardless of when the flag flipped
  relative to the investigation start.
- **Chart pipeline still on Pydantic Graph after this lands.** Mixed
  framework state for ~weeks/months until chart migration runs.
  *Mitigation*: shared typed observability layer (Phase 1) means
  Langfuse traces from both frameworks look identical; CLAUDE.md update
  in T52 documents the temporary mixed state and points to chart-
  migration plan as the next step.
- **Replay-bundle redundancy with checkpointer.** ExecutionTracer captures
  a frozen replay artefact; checkpointer captures resume state.
  *Mitigation*: T43 asserts both populate without conflict; documented
  separation already in umbrella plan.

## Verification

End-to-end after each phase:

```bash
just lint                      # ruff + mypy + import-linter
just test                      # unit tests pass
just test-integration          # full LangGraph runs with MemorySaver + AsyncPostgresSaver
just test-evals                # functional parity: side-by-side reply equivalence
just docker-compose-up         # local Langfuse v3
# Trigger /api/sre/investigate, observe in Langfuse:
#  - one Session per request_id grouping legacy + workflow during overlap
#  - gen_ai.* attributes populated on every agent span
#  - Generation views show input/output tokens + sentinel.cost_usd
#  - approval-pending traces show interrupt() event; resume traces appended
```

Specific fixture (T38) — `tests/integration/test_sre_workflow_interrupt.py`:
seed an alert that scores below the approval threshold, run graph, assert
`__interrupt__` payload returned, post Slack approval mock,
`graph.ainvoke(Command(resume=...))` finishes the run, final
`InvestigationOutcome.findings_published == True`, and Postgres
`langgraph.checkpoints` table contains both pre-resume and post-resume
checkpoints for the same `thread_id`.

## Conventions to Honour

- Module imports only (`from sentinel.X import Y`; reference as
  `Y.symbol`); no inline imports inside functions.
- `attrs.frozen(kw_only=True, slots=True)` for domain primitives;
  Pydantic `BaseModel(frozen=True)` for the observability boundary types
  (per `python.md` exception); `@dataclasses.dataclass` for any node-deps
  containers (matches `interfaces/graphs/agents/*.Dependencies` pattern);
  `TypedDict(total=False)` for LangGraph state (matches
  `support_state_mod`).
- File-size budget 200–400 typical, 800 max — flat layout for
  `sre_investigation.py` first; if it exceeds 800 lines after T17–T23,
  extract to `_sre_nodes/` in a follow-up. Prefer small focused files.
- Structured logging only (`logs.log_event` / `logs.log_exception`).
- Keyword-only public functions in `application/` and `interfaces/`
  entrypoints.
- Docstrings start with verb-first imperative ("Return..." / "Raise...").
- Domain code reads from `get_config()`, never `settings` directly; new
  env vars surface on `BaseConfiguration` via `@property`.
- All new agent.run callsites must call `extract_usage(...)` and stamp
  `UsageAttributes` on the agent span — non-negotiable for T17–T23 and
  for the legacy retrofits in T9–T10.

## Changes

| Date | What changed | Why |
|---|---|---|
| 2026-04-29 | Initial draft | Sub-plan under umbrella `pydanticai-langgraph-adoption.md`; design aligned with ADR 0007 and the support-migration patterns shipped in PR(N+1) |

## Outcome

_Fill in after completion._

### What was delivered

- ...

### Follow-up / tech debt

- Chart-coding migration plan — kicks off after this lands
- LangGraph checkpoint TTL / cleanup job — captured in umbrella; needs own plan
- Per-tenant or per-team workflow routing — month-3+ review
- If `sre_investigation.py` grows past 800 lines, extract per-node
  helper modules under `interfaces/workflows/_sre_nodes/`
