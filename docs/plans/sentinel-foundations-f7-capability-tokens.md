# Plan: Sentinel Foundations F7 — Capability Tokens for Tool Authorization

**Status:** draft
**Created:** 2026-04-29
**Last updated:** 2026-04-29
**Parent plan:** [`sentinel-hedgefund-foundations.md`](sentinel-hedgefund-foundations.md) Phase F7
**RFC reference:** §5.3, R-TL-3, R-TL-4
**Depends on:** F6 runbook catalog (PR #31, merged) — F7 reads `Runbook.tools.allowed_tool_names` / `tool_max_calls` / `max_total_tool_calls`. F3.8 (`agent_calls.capability_token` column) — already shipped.

## Goal

Ship the F7 phase of the foundations: tools are authorised only when the **active runbook** lists them, and tool invocations whose `namespace` argument disagrees with the **envelope's tenant** are rejected. Both rejections emit a structured `tool_grant_denied` event and write an `audit_log` row so the regulator answer to "why was this call refused?" lives in one queryable place.

Per the F6 contract update (foundations plan §F7.2 verdict), enforcement happens at the **toolset wrapper boundary** — never at function entry — because function-entry checks are bypassable by indirect prompt injection re-entering the toolset (Cerbos / OWASP / SuperTokens guidance).

## Scope

### In scope

- `src/sentinel/domain/tools/grants.py`: frozen attrs `RunbookGrant`, exception types `ToolNotInRunbookError` / `TenantScopeViolationError` / `ToolBudgetExceededError`, pure-function `authorize_tool_call(*, runbook, tool_name, tenant_id, call_namespace) -> RunbookGrant`
- `src/sentinel/plugins/toolsets/_runbook_scope.py`: `RunbookScopedToolset` (`WrapperToolset`) wrapping every team toolset; intercepts `call_tool`, runs `authorize_tool_call`, enforces per-tool `max_calls` + cumulative `max_total_tool_calls`, then delegates. Reads active runbook + envelope from `RunContext.deps`. On rejection: `logs.log_event("tool_grant_denied", ...)` + `record_audit_entry(action="tool_call_rejected", ...)` + raise. Public factory `wrap_for_runbook_scope(toolset, *, label)` mirroring `wrap_for_replay`
- Wire `envelope: Envelope` (already-present `runbook: Runbook | None`) into the investigator agent `Dependencies` for `k8s_investigator`, `root_cause_analyser`. Add a counter dict for per-run budget tracking
- `config.load_agents` wiring: every team toolset is wrapped via `wrap_for_runbook_scope(...)` first, then `wrap_for_replay(...)` outside (so capability rejections still surface in the replay capture as a recorded error — replay determinism preserved)
- Stamp the produced `RunbookGrant` onto the `record_tool_call` entry so the F3.8 `agent_calls.capability_token` column gets populated wherever `AgentCallRecord` rows land
- Tests: `tests/unit/domain/tools/test_grants.py` (validator + token shape), `tests/unit/plugins/toolsets/test_capability_wrapper.py` (wrapper integration with mock toolset + audit writer + counter state), `tests/integration/test_tenant_isolation.py` (the two adversarial slice cases from F7.5 with a real DB)
- Docs: `docs/architecture.md` §Capability Tokens subsection (token shape, rejection types, audit-row schema, layering diagram), `docs/prd.md` R-TL-3 / R-TL-4 checkbox tick, `docs/plans/INDEX.md` entry, parent plan progress note

### Out of scope (follow-on plans)

- K8s RBAC layer enforcing tenant isolation at the cluster boundary (RFC §10 / wk5 plan — F7 covers the **app-layer** slice only)
- Full adversarial fixture suite (RFC §14 wk7) — F7.5 is the foundations slice (two cases)
- LLM judge for tool-rejection rationale (deferred to month 3 per RFC §14.2)
- Per-tool granular argument linting beyond `namespace=` matching (call-shape policies in RFC §5.3 follow-on)
- Cross-team toolset sharing rules (week 5+ when DevOps / ACE profiles activate)
- Live-DB integration test for the wrapper itself (covered by F7.5; unit tests use a stub audit writer)

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Where the token type lives | `domain/tools/grants.py` | Only `domain/` and above consume it; `config` doesn't need to compose it onto `BaseConfiguration`, so `data/primitives/` doesn't earn its keep here. Plan F7.1 explicitly nominates this path. |
| Where the wrapper lives | `plugins/toolsets/_runbook_scope.py` (leading underscore — internal to package) | Mirrors `_runtime.py:ReplayCapturingToolset` — wrapper is a runtime concern of the plugins/toolsets package; depends on the PydanticAI `WrapperToolset` external class. |
| Enforcement boundary | Toolset wrapper, **not** function entry | F6 contract update — function-entry guards bypassable by indirect prompt injection re-entering the toolset (Cerbos / OWASP / SuperTokens). |
| Wrapper layering | `wrap_for_replay(wrap_for_runbook_scope(toolset))` — replay outer, capability inner | If capability rejects, the replay layer records `<error: ToolNotInRunbookError>` then re-raises (existing `ReplayCapturingToolset.call_tool` already does this for any wrapped exception). Replay determinism preserved; rejections show up in replay diffs. |
| Per-run counter state | Mutable `_tool_call_counters: dict[str, int]` field on `Dependencies` (`field(default_factory=dict)`) | Run-scoped naturally — fresh `Dependencies` per pipeline run. Avoids `ContextVar` complexity since deps are already threaded through PydanticAI `RunContext`. |
| Rejection rejection types | `ToolNotInRunbookError` (tool not in runbook OR in `denied_tools`) / `TenantScopeViolationError` (cross-tenant `namespace=`) / `ToolBudgetExceededError` (per-tool or total budget hit) | Three discriminating types let the audit row + log event carry an unambiguous `rejection_kind`; downstream reporting can group by type. |
| Tenant matching | `call_namespace and call_namespace != tenant_id` → reject | Plan F7.1 spec verbatim. `None` `call_namespace` is allowed (tools that don't take a namespace, e.g. cluster-wide reads — guarded separately by `allowed_tool_names`). |
| Token shape | `RunbookGrant(runbook_id, runbook_content_sha, tool_name, tenant_id, granted_at)` — frozen attrs | `runbook_content_sha` extends plan F7.1 minimum so the audit trail captures **which version** of the runbook authorised the call; pairs naturally with F6's triple-key versioning. |
| Audit row schema | `actor="tool_runtime"`, `action="tool_call_rejected"`, `resource_type="tool"`, `resource_id=tool_name`, `details_json={rejection_kind, runbook_id, runbook_content_sha, tenant_id, call_namespace, request_id, attempted_at}` | Matches existing `domain/audit/operations.py:record_audit_entry` signature; `details_json` carries the discriminating context without column proliferation. |
| Token persistence | Pass token via `record_tool_call(..., capability_token=str(token))` extension | F3.8 column already exists; downstream `AgentCallRecord` writers fill it from the recorded entry. F7 produces the token; persistence wiring is light. |
| Default-deny stance | Wrapper rejects if no runbook is bound on `Dependencies` (active runbook is `None`) **only when** `runbook_strict=True`; default `False` for foundations | Foundations soft-fail — many existing pipelines run without a runbook (no-match path). Strict mode is opt-in via `Settings.runbook_scope_strict_mode` (new field, default False) to be ratcheted on later. |
| Tests/factories | Use existing `make_runbook()` if present in `tests/factories/__init__.py`, otherwise add one; envelope via `Envelope(...)` direct construction in tests | Factory pattern keeps test data construction one-line. |

## Steps

### F7.1 — Capability primitives

- [ ] **F7.1.1** Author `src/sentinel/domain/tools/__init__.py` (if not present — currently the package has only the toolset modules; we'll add a sub-namespace cleanly)
- [ ] **F7.1.2** Author `src/sentinel/domain/tools/grants.py`:
    - Frozen attrs `RunbookGrant(runbook_id: str, runbook_content_sha: str, tool_name: str, tenant_id: str, granted_at: datetime)` with kw_only + slots
    - Exceptions: `RunbookAuthorizationError` (base) + `ToolNotInRunbookError(tool_name, runbook_id)` + `TenantScopeViolationError(tenant_id, call_namespace)` + `ToolBudgetExceededError(tool_name, max_calls, scope)`. Each carries the rejection context as kw-only attrs.
    - Pure function `authorize_tool_call(*, runbook: Runbook, tool_name: str, tenant_id: str, call_namespace: str | None = None, now: datetime | None = None) -> RunbookGrant`
        - Reject if `tool_name in runbook.tools.denied_tools` → `ToolNotInRunbookError(reason="denied")`
        - Reject if `tool_name not in runbook.tools.allowed_tool_names` → `ToolNotInRunbookError(reason="not_listed")`
        - Reject if `call_namespace is not None and call_namespace != tenant_id` → `TenantScopeViolationError`
        - Return `RunbookGrant(runbook_id=runbook.runbook_id, runbook_content_sha=runbook.metadata.content_sha, tool_name=tool_name, tenant_id=tenant_id, granted_at=now or datetime.now(UTC))`
- [ ] **F7.1.3** TDD `tests/unit/domain/tools/test_grants.py`: cover (a) tool not in runbook → unauthorized, (b) tool in denied_tools → unauthorized, (c) cross-tenant → forbidden, (d) same-tenant + listed → token returned with all five fields populated, (e) `call_namespace=None` is permitted. GWT comments per `testing.md` rule. Use a `make_runbook()` factory fixture (extend `tests/factories/__init__.py` if needed).

### F7.2 — Toolset wrapper

- [ ] **F7.2.1** Author `src/sentinel/plugins/toolsets/_runbook_scope.py`:
    - `class RunbookScopedToolset(WrapperToolset[Any])` with `__init__(wrapped, *, label, audit_writer)` where `audit_writer` defaults to `domain.audit.operations.record_audit_entry` (constructor injection so unit tests can stub)
    - `async def call_tool(name, tool_args, ctx, tool)`:
        1. Resolve `runbook = ctx.deps.runbook`, `envelope = ctx.deps.envelope`, `counters = ctx.deps._tool_call_counters` from `RunContext.deps`
        2. If `runbook is None`: pass-through with `logs.log_event("runbook_scope_check_skipped", ...)` (foundations soft-fail)
        3. Call `authorize_tool_call(runbook=runbook, tool_name=name, tenant_id=envelope.tenant_id, call_namespace=tool_args.get("namespace"))` — on `RunbookAuthorizationError`, log + audit + re-raise
        4. Budget check: `total = sum(counters.values())`, `per = counters.get(name, 0)`. Raise `ToolBudgetExceededError` if `per >= runbook.tools.tool_max_calls.get(name, ∞)` or `total >= runbook.tools.max_total_tool_calls`. Log + audit + re-raise.
        5. Tag span attribute `capability.runbook_id`, `capability.tool_name`, `capability.tenant_id` for OTel correlation
        6. Increment counter, attach token to `record_tool_call(..., capability_token=str(token))` (extend `record_tool_call` signature in `_runtime.py` to accept and forward the token), delegate to `wrapped.call_tool(...)`, return result
        7. Exception path: counter still increments (so budget caps include failed calls); propagate exception
    - `def wrap_for_runbook_scope(toolset, *, label, audit_writer=None) -> AbstractToolset[Any]` factory mirroring `wrap_for_replay` (idempotent — already-wrapped toolsets returned as-is; `None` → `None`)
- [ ] **F7.2.2** Extend `src/sentinel/plugins/toolsets/_runtime.py:record_tool_call` signature with `capability_token: str | None = None` kwarg; thread it onto `ToolIOEntry` (add the field to `ReplayBundle.ToolIOEntry`). Default `None` keeps backward compat for direct callers.
- [ ] **F7.2.3** TDD `tests/unit/plugins/toolsets/test_capability_wrapper.py`: cover (a) tool not in runbook → wrapped exception + audit_writer called once with kwargs matching schema, (b) cross-tenant `namespace` arg → forbidden + audit, (c) successful call → counter increments + delegates + token recorded, (d) per-tool budget exceeded → ToolBudgetExceededError + audit, (e) total budget exceeded → ToolBudgetExceededError + audit, (f) `runbook is None` (foundations soft-fail) → pass-through with skipped event. Use `unittest.mock.AsyncMock` for the wrapped toolset + audit writer; assert `record_tool_call` is invoked with `capability_token` kwarg on success.

### F7.3 — Wire envelope into Dependencies + config wiring

- [ ] **F7.3.1** Add `envelope: envelope_mod.Envelope | None = None` and `_tool_call_counters: dict[str, int] = dataclasses.field(default_factory=dict)` to `Dependencies` in `src/sentinel/interfaces/graphs/agents/k8s_investigator.py`
- [ ] **F7.3.2** Same fields on `Dependencies` in `src/sentinel/interfaces/graphs/agents/root_cause_analyser.py`
- [ ] **F7.3.3** Update call-sites in `src/sentinel/interfaces/graphs/investigation.py` to thread `envelope=ctx.state.envelope` into the deps for both agents (analyser already constructs deps; k8s investigator likewise via the holmes_adapter / k8s_native paths)
- [ ] **F7.3.4** Update `src/sentinel/plugins/common/config.py` (or wherever `load_agents` / toolset wiring happens) so each team toolset is wrapped via `wrap_for_runbook_scope(...)` first, then `wrap_for_replay(...)` outside. Idempotent for already-wrapped instances.
- [ ] **F7.3.5** Add `Settings.runbook_scope_strict_mode: bool = False` env-var with surfaced `@property` on `BaseConfiguration` (mirror existing pattern). When True, the wrapper rejects calls with `runbook is None` instead of soft-passing. Default False for foundations; ratcheted on after F8.

### F7.4 — Unit tests (covered by F7.1.3 + F7.2.3)

- F7.1.3 and F7.2.3 are the F7.4 unit-test surface. Re-listed here as a verification gate before F7.5.

### F7.5 — Adversarial integration slice

- [ ] **F7.5.1** Author `tests/integration/test_tenant_isolation.py`:
    - **Case A — cross-tenant**: build a runbook listing `k8s_get_pod_logs`, build envelope with `tenant_id="pm-a"`, build a real `RunbookScopedToolset` wrapping a stub k8s toolset, call `k8s_get_pod_logs(namespace="other-pm")` via the wrapper. Assert `TenantScopeViolationError` raised AND a row exists in `audit_log` with `action="tool_call_rejected"`, `details_json` containing `rejection_kind="forbidden_tenant"`, `tenant_id="pm-a"`, `call_namespace="other-pm"`.
    - **Case B — tool not in runbook**: build a runbook listing only `k8s_*` tools, attempt to call `prom_query_range`. Assert `ToolNotInRunbookError` AND audit_log row with `rejection_kind="not_listed"`, `tool_name="prom_query_range"`, `runbook_id` recorded.
    - Use real DB session per `tests/integration` conventions; tear down via the existing fixture.

### F7.6 — Documentation

- [ ] **F7.6.1** Add §Capability Tokens subsection to `docs/architecture.md` after the §Runbooks subsection. Cover: token shape, rejection types, wrapper boundary diagram (Replay → Capability → Underlying), audit-row schema, default-deny stance + strict-mode env-var.
- [ ] **F7.6.2** Update `docs/prd.md`: tick R-TL-3 (capability tokens enforced at toolset-wrapper boundary) and R-TL-4 (cross-tenant rejection at app layer; K8s RBAC layer in wk5 follow-on).
- [ ] **F7.6.3** Update `docs/plans/INDEX.md`: move this plan from "Draft" to "In Progress" → "Complete" on PR merge; add row.
- [ ] **F7.6.4** Update parent plan `sentinel-hedgefund-foundations.md`: tick F7.1–F7.6 checkboxes; bump Progress header to `7/9 phases`; add Changes table row.

## Acceptance

- R-TL-3 met: tools outside `runbook.tools.allowed_tool_names` rejected with structured error + `audit_log` row carrying `rejection_kind`, `runbook_id`, `tool_name`.
- R-TL-4 met (app-layer slice): cross-tenant `namespace=` arg rejected with structured error + `audit_log` row.
- Replay determinism preserved: rejected calls appear in the replay bundle as `<error: ...>` entries (existing `ReplayCapturingToolset` behaviour) so 30-run determinism CI continues to pass.
- F3.8 `agent_calls.capability_token` column populated on every successful tool call (via the extended `record_tool_call` signature).

## Test surface (estimated)

- ~6–8 unit tests in `test_grants.py` (validator + token shape)
- ~6 unit tests in `test_capability_wrapper.py` (wrapper + budget + audit + soft-fail)
- 2 integration tests in `test_tenant_isolation.py` (the adversarial slice)
- 1 negative test for the `record_tool_call` signature extension (capability_token round-trips through `ToolIOEntry`)

Total: ~15 new tests. F6 baseline: 272 unit + 8 integration tests pass; F7 must keep that baseline + add the new tests on top.

## Branch strategy

Single feature branch `feat/sentinel-foundations-f7-capability-tokens`. Atomic commits per F7.X step. Open one PR with the full F7 deliverable on completion.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-29 | Initial draft | F6 merged (PR #31); F7 unblocked. Sub-plan extracted from foundations §F7. |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
