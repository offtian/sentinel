# Plan: Sentinel Hedge Fund — Foundations (RFC-001 v0.4)

**Status:** in-progress
**Created:** 2026-04-25
**Last updated:** 2026-04-26
**Progress:** F0 deferred · F1 complete (PR #22) · F2 complete · F3 in-progress (F3.1–F3.3 done)

## Goal

Evolve the existing Sentinel codebase into the foundations of the hedge-fund-grade SRE platform described in `Sentinel/RFC-001-sentinel-hedgefund.md` v0.4. "Foundations" means RFC §14 weeks 0.5–4: validation sprint, config layering, identity propagation, the OTEL → Langfuse → replay-bundle triple (the single failure mode to fear, §14.7), LiteLLM proxy migration, runbook catalog, capability tokens, and a deterministic groundedness gate. Weeks 5–8 (real cluster, Helm, HolmesGPT integration breadth, adversarial suite, soft launch) become a follow-up plan.

The strategy is **evolve in place**, not greenfield. RFC §15.14 v0.4 selects PydanticAI + LangGraph; the current codebase is already PydanticAI + Pydantic Graph. The orchestration-framework migration (Pydantic Graph → LangGraph) is intentionally deferred behind an ADR and is **not** in foundations scope — the replay determinism payoff is real but the framework swap mid-foundations would block every other phase.

## Scope

### In scope

- Validation sprint for tentative decisions D-11..D-16 + O-10 (each gets a one-page ADR with named-owner sign-off)
- Config layering refactor: two-layer chain `Settings` → `BaseConfiguration` (Pydantic, layered fields + firm-wide defaults) → `CommonConfiguration` (concrete vendor wiring in `plugins/common/`). Multi-tenant via `Settings.team_profile` discriminator + `TEAM_CONFIG_REFS` registry. Team-specific subclasses (`SRETeamConfig`, etc.) deferred until profiles diverge.
- DB schema gap-fill for the 8 RFC-canonical tables: `alert_request`, `runbook_match`, `investigation`, `finding`, `tool_call`, `investigation_task`, `quality_verdict`, `audit_log`
- Identity propagation: `request_id` UUID minted at FastAPI ingress, `tenant_id` / `region` / `pii_class` carried through every span and DB row
- OTEL → Langfuse triple: mandatory span attribute set, Langfuse OTLP exporter, replay-bundle determinism (the §14.7 failure mode)
- LiteLLM proxy migration: SDK in-process → network proxy via `base_url` + virtual key
- Runbook catalog format (RUNBOOK.md + tools.yaml + checks.yaml + tests.yaml) with one reference runbook + tag-based matcher
- Capability tokens: tools authorized only when active runbook lists them; tenant-scoped tool execution
- Quality gate (deterministic groundedness only): every Finding has ≥1 evidence_ref pointing at a recorded tool_call
- Replay CLI + 30-run determinism CI test

### Out of scope (follow-up plans)

- Real K8s cluster deployment, Helm chart finalisation (RFC §14 wk 5, §6.5)
- HolmesGPT integration breadth beyond the existing adapter (RFC §14 wk 6 stretch)
- LLM judge in quality gate (RFC §14.2 cut, slips to month 3)
- Adversarial fixture suite (RFC §14 wk 7)
- Per-PM Langfuse RBAC + 5-layer info barrier layers 3–5 (RFC §5.7, month 3+)
- DevOps + ACE team profiles (RFC §1.4, months 4–6)
- Case-history retrieval with pgvector (RFC §3.3.1, needs ≥100 confirmed investigations first)
- WORM archive job for `audit_log` (RFC §12.3.10, wk 5+)
- Full 30-tool catalogue (RFC §5.2, wk 5+)
- Slack interactive UI for approval buttons (data structures only in foundations)
- LangGraph migration (deferred behind ADR 0007 — revisit at month 3 once replay determinism is proven)

### Already shipped (no-op for this plan)

| RFC reference | Existing artefact |
|---|---|
| §2.4 LiteLLM SDK in-process | PR #17 LiteLLM SDK migration — needs F5 evolution to proxy network mode |
| §3.6 confidence + approval gate | `domain/confidence/`, `domain/approval/` |
| §3.7 publish | `domain/investigations/publish.py` + Slack/PagerDuty adapters |
| §3.8 trace bundle (partial) | PR #15 prompt-versioning-and-replay |
| §4.5 skills runtime | `domain/skills/` (runbooks layer in F6 sits above skills) |
| §5.8 HolmesGPT | `domain/investigations/holmes_adapter.py` |
| §10.4 K8s investigation backends | `domain/investigations/k8s_native_agent.py`, `kagent_adapter.py` (PR #20) |
| §10.5 OTEL spans (partial) | `data/tracing_models.py`, `bootstrap_otel.py` |
| Token usage + cost | PR #18 |

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Repo strategy | Evolve existing Sentinel codebase in place | RFC §15.14 v0.4 picks PydanticAI + LangGraph — current stack is already PydanticAI; preserves runbook/MCP/K8s/audit work; framework deltas isolate to LiteLLM transport, config layering, and OTEL/Langfuse wiring |
| Agent framework | PydanticAI (no change) | RFC D-01 v0.4 confirms; `instrument=True` already wired across agents |
| Orchestration framework | Stay on Pydantic Graph for foundations; revisit LangGraph at month 3 (ADR 0007) | LangGraph's checkpoint replay is attractive but framework swap mid-foundations would block F4–F8; PR #15 already covers replay determinism via the bundle approach |
| Strangler vs big-bang | Strangler everywhere | Existing pipelines must keep working at every phase boundary; LiteLLM proxy + runbook catalog + capability tokens coexist with current code until F8 |
| Config layering | Two-layer chain on the existing Pydantic `BaseConfiguration`: layered fields + firm-wide defaults on `BaseConfiguration`, vendor wiring on `CommonConfiguration` in `plugins/common/`. Multi-tenant via `Settings.team_profile` + `TEAM_CONFIG_REFS` registry. Team subclasses (`SRETeamConfig`) earn their keep when behaviour diverges. | Pivoted from the original 4-layer attrs chain — Pydantic stays the single config contract, `team_id` is a property reading `settings.team_profile`, dispatch happens via `importlib` so `config` never depends on `plugins`. |
| Identity propagation | `request_id` minted by FastAPI middleware; `tenant_id`/`region`/`pii_class` carried in `Envelope` frozen attrs from ingress | Matches RFC §3.1 + R-IN-3; `Envelope` is the single object every node reads tenant identity from |
| DB schema | Add 4 missing canonical tables; migrate 4 existing tables (column adds, no data loss) | Existing `InvestigationRecord` ≈ `investigation`, `AuditLogRecord` ≈ `audit_log`, `AgentCallRecord` ≈ `tool_call` — rename + extend rather than duplicate |
| Replay determinism | Extend PR #15 bundle with tool I/O snapshots; CI runs 30-iteration determinism test | Bit-for-bit reproducibility (R-AG-4); 30 runs balances coverage vs CI time |
| Quality gate | Deterministic groundedness only; no LLM judge in foundations | RFC §14.2 cuts LLM judge to month 3; deterministic check is sufficient for shadow mode |
| Capability tokens | Validated at the top of every tool function, before vendor call | RFC §5.3 — token derived from active runbook + envelope tenant_id; rejection emits structured error + audit_log row |
| Runbook ↔ skills relationship | Both layers coexist: runbook = top-level investigation contract (RFC §4); skill = behavioural prompt fragment (RFC §15.10) | RFC §4.5 explicitly says both, at different layers; runbook owns tool/check authorization, skill composes system prompts |
| Validation sprint output | One-page ADR per decision in `docs/adrs/`, mirroring RFC D-* and O-* numbering | RFC §11.4 — flips become amendments, not restarts |
| Plan ownership | Solo engineer, foundations span weeks 0.5–4 of RFC §14 (≈3.5 working weeks) | Single profile (SRE), single dev cluster, single region — avoids the §14.6 anti-patterns |

## Architecture

### Foundations layer cake (RFC §14 weeks 0.5–4)

```
Phase F0 — Validation sprint (D-11..D-16, O-10)              wk 0.5
              ↓
Phase F1 — Config layering: Settings → BaseConfiguration → CommonConfiguration      wk 1
              ↓
Phase F2 — Identity & envelope propagation (request_id, tenant_id, pii_class)       wk 1
              ↓
Phase F3 — DB schema gap-fill: 8 canonical tables, audit_log WORM-shape             wk 1-2
              ↓
Phase F4 — OTEL → Langfuse → replay-bundle triple (single failure mode)             wk 2-3
              ↓
Phase F5 — LiteLLM proxy migration + orchestration framework decision (ADR)         wk 2
              ↓
Phase F6 — Runbook catalog (RUNBOOK.md + tools/checks/tests yaml) + tag matcher     wk 3
              ↓
Phase F7 — Capability tokens for tool authorization                                  wk 3
              ↓
Phase F8 — Quality gate (deterministic groundedness) + replay determinism CI         wk 4
```

F0 gates everything (decisions can flip downstream phases). F1 unblocks F4 (configs carry the team_id span attribute). F3 unblocks F4 (mandatory attrs need columns to land in). F5 is parallel-runnable with F3/F4. F6/F7 share a touchpoint in the pipeline. F8 closes the loop.

### What's missing (the foundations gap)

| RFC reference | Gap |
|---|---|
| §1.4 multi-team profiles | Layered fields + firm-wide defaults must live on `BaseConfiguration`, with `Settings.team_profile` + `TEAM_CONFIG_REFS` dispatch so DevOps/ACE can plug in via a registry entry. Team subclasses arrive when behaviour diverges. |
| §3.1 envelope | `request_id` not propagated to spans/DB; `tenant_id` and `pii_class` absent from existing models |
| §3.2 alert_request | Stage 1 has no canonical row — webhook handler writes nothing until pipeline starts |
| §3.3 runbook_match | No runbook table; skills are the closest analog but don't carry match_method/match_confidence |
| §3.5 investigation_task | Agent loop is unstructured; no task-list table |
| §3.6 quality_verdict | Confidence row exists but lacks groundedness audit |
| §4.2 RUNBOOK.md + tools/checks/tests yaml | Runbook directory format does not exist |
| §5.3 capability tokens | Tools authorized by tool registry only — no runbook-level gate |
| §5.4 evidence_refs check | Confidence scoring exists; explicit groundedness gate as a pipeline node does not |
| §13 OTEL → Langfuse | OTel emits to console/Logfire only; Langfuse not wired |
| §3.8 deterministic replay | PR #15 captures prompts + LLM I/O; tool I/O + 30-run CI determinism missing |
| §2.4 LiteLLM proxy | LiteLLM SDK is in-process; no `base_url`/virtual-key plumbing |

### File map

#### Created

```
docs/adrs/                                                  # F0
docs/adrs/0001-D11-on-prem-only.md
docs/adrs/0002-D12-monorepo.md
docs/adrs/0003-D13-firm-shared-infra.md
docs/adrs/0004-D15-langfuse-rbac.md
docs/adrs/0005-D16-postgres-pgvector.md
docs/adrs/0006-O10-pydanticai-langgraph.md
docs/adrs/0007-orchestration-framework.md                   # F5 outcome

src/sentinel/plugins/common/__init__.py                     # F1: substrate package marker
src/sentinel/data/policies.py                               # F1: ApprovalPolicy + OutputChannel + RedactionPolicy frozen primitives
                                                            # (Team subclasses src/sentinel/plugins/teams/<name>/config.py
                                                            #  deferred to a later plan — they earn their keep when team
                                                            #  behaviour actually diverges.)

src/sentinel/domain/envelope.py                             # F2: Envelope frozen attrs
src/sentinel/interfaces/api/middleware.py                   # F2: RequestIdMiddleware

src/sentinel/data/alert_request_models.py                   # F3
src/sentinel/data/runbook_models.py                         # F3
src/sentinel/data/task_models.py                            # F3
src/sentinel/data/quality_models.py                         # F3
src/sentinel/data/migrations/alembic/versions/008_alert_request_table.py
src/sentinel/data/migrations/alembic/versions/009_runbook_match_table.py
src/sentinel/data/migrations/alembic/versions/010_investigation_task_table.py
src/sentinel/data/migrations/alembic/versions/011_quality_verdict_table.py
src/sentinel/data/migrations/alembic/versions/012_audit_log_worm_constraints.py
src/sentinel/data/migrations/alembic/versions/013_extend_investigation_tool_call.py

src/sentinel/utils/langfuse_export.py                       # F4: Langfuse OTLP exporter
src/sentinel/utils/replay_bundle.py                         # F4: extended bundle (tool I/O)
src/sentinel/replay_cli.py                                  # F4: `python -m sentinel.replay <id>`

src/sentinel/domain/runbooks/__init__.py                    # F6
src/sentinel/domain/runbooks/models.py                      # F6: Runbook, ToolSpec, CheckSpec, TestSpec
src/sentinel/domain/runbooks/loader.py                      # F6
src/sentinel/domain/runbooks/matcher.py                     # F6: tag-based matcher

src/sentinel/plugins/teams/sre/runbooks/k8s-crashloop/RUNBOOK.md     # F6: reference runbook
src/sentinel/plugins/teams/sre/runbooks/k8s-crashloop/tools.yaml
src/sentinel/plugins/teams/sre/runbooks/k8s-crashloop/checks.yaml
src/sentinel/plugins/teams/sre/runbooks/k8s-crashloop/tests.yaml

scripts/compute_runbook_shas.py                             # F6: pre-commit hook

src/sentinel/domain/tools/capabilities.py                   # F7

src/sentinel/domain/quality/__init__.py                     # F8
src/sentinel/domain/quality/groundedness.py                 # F8

tests/integration/test_request_id_propagation.py            # F2
tests/integration/test_8_canonical_tables.py                # F3
tests/integration/test_replay_determinism.py                # F4 + F8 CI
tests/integration/test_litellm_proxy.py                     # F5
tests/integration/test_tenant_isolation.py                  # F7
tests/unit/test_config_layering.py                          # F1
tests/unit/test_envelope.py                                 # F2
tests/unit/test_runbook_loader.py                           # F6
tests/unit/test_runbook_matcher.py                          # F6
tests/unit/test_capability_tokens.py                        # F7
tests/unit/test_groundedness.py                             # F8
```

#### Modified

```
src/sentinel/settings.py                                    # F1: env-only additions (team_profile, litellm_*, langfuse_*, otel_collector_endpoint, runbooks_root)
src/sentinel/config.py                                      # F1: layered fields + firm-wide defaults on BaseConfiguration, TEAM_CONFIG_REFS dispatch via importlib
src/sentinel/plugins/config.py → src/sentinel/plugins/common/config.py    # F1: rename in-place; concrete CommonConfiguration moves alongside the future shared substrate

src/sentinel/data/audit_models.py                           # F3: WORM constraints + request_id
src/sentinel/data/models.py                                 # F3: extend InvestigationRecord toward `investigation` shape
src/sentinel/data/tracing_models.py                         # F3: extend AgentCallRecord toward `tool_call` shape
src/sentinel/bootstrap_otel.py                              # F4: Langfuse exporter wiring
src/sentinel/utils/replay.py                                # F4: thin shim, delegates to replay_bundle.py

src/sentinel/interfaces/graphs/investigation.py         # F2/F6/F7/F8: envelope propagation, MatchRunbook node, capability gate, AssessQuality node
src/sentinel/interfaces/graphs/_node_helpers.py             # F4: mandatory span attributes from envelope
src/sentinel/interfaces/graphs/agents/k8s_investigator.py   # F4 + F6: runbook in deps; instrument audit
src/sentinel/interfaces/graphs/agents/alert_classifier.py   # F4: span attributes
src/sentinel/interfaces/graphs/agents/root_cause_analyser.py  # F4: span attributes

src/sentinel/plugins/toolsets/kubernetes.py                 # F7: capability token check
src/sentinel/plugins/toolsets/observability.py              # F7: capability token check

src/sentinel/domain/confidence/                             # F8: emits QualityVerdict alongside Confidence

.env.default                                                # F1 + F4 + F5: TEAM_PROFILE, LITELLM_BASE_URL, LITELLM_VIRTUAL_KEY, LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, RUNBOOKS_ROOT
docs/architecture.md                                        # All phases
docs/prd.md                                                 # Acceptance-criteria checkboxes for the foundations R-* requirements
pyproject.toml                                              # F1: import-linter contracts for plugins/teams + plugins/common
```

## Steps

### Phase F0: Validation sprint (D-11..D-16, O-10)

Maps to RFC §14 week 0.5. No code change; produces ADRs that may amend the RFC. Do this **first** because outcomes can flip phases F1, F4, F5.

- [ ] **F0.1** Schedule Day-1..Day-5 conversations per RFC §11.4: compliance lead (D-11), platform tech lead for monorepo onboarding (D-12), LiteLLM operator (D-13), Langfuse operator (D-15), DBA (D-16), senior engineer pushing PydanticAI + LangGraph (O-10). Calendar slots, agenda + RFC sections to bring per the §11.4 "things to bring" checklist
- [ ] **F0.2** Day 1 — monorepo onboarding (D-12). Pair with platform TL; capture CI/CD config, lint/typecheck rules, an example service. Output: `docs/adrs/0002-D12-monorepo.md` deciding stay-as-greenfield-repo OR sub-package-in-monorepo. Fallback if repo-style ≠ monorepo: half-day CI scaffold delta only
- [ ] **F0.3** Day 2 — compliance LLM policy (D-11). Read signed policy doc; confirm on-prem-only constraint and approved-on-prem-model list. Output: `docs/adrs/0001-D11-on-prem-only.md`. Fallback if external+VPC OK: noted but not implemented in foundations (~3 days extra in month 3)
- [ ] **F0.4** Day 2–3 — agent framework re-eval (O-10). Meet senior engineer pushing PydanticAI + LangGraph; review their POC; agree decision. Output: `docs/adrs/0006-O10-pydanticai-langgraph.md`. Default position from RFC v0.4: confirm PydanticAI + LangGraph; orchestration-framework migration is separately decided in F5 (ADR 0007)
- [ ] **F0.5** Day 3 — LiteLLM operator (D-13 partial). Request virtual key with `tenant_id`/`team_profile`/`pii_class` tags; confirm OTLP routing to firm Langfuse; get list of tool-use-validated on-prem models. Output: connection details captured (kept in 1Password / CI secret store, not in repo); ADR `0003-D13-firm-shared-infra.md`
- [ ] **F0.6** Day 4 — Langfuse operator (D-15) + DBA (D-16). Langfuse: confirm RBAC features sufficient for per-team projects + tag-based filtering on tenant_id. Postgres: confirm `pgvector` extension and per-database role separation for sentinel_app + sentinel_audit. Outputs: ADRs `0004-D15-langfuse-rbac.md`, `0005-D16-postgres-pgvector.md`. Fallback if Langfuse RBAC weak: one Langfuse per team (3 instances). Fallback if shared Postgres lacks pgvector: dedicated small RDS for case-history only (case-history is out of foundations scope, so this becomes a month-3 problem, not a blocker)
- [ ] **F0.7** Day 5 — amend RFC + this plan with any flipped decisions. Update `Sentinel/RFC-001-sentinel-hedgefund.md` D-11..D-16 status table to `(confirmed)` or `(amended — see ADR-XXXX)`. If F1/F4/F5 phasing changes due to a flip, update this plan's "Steps" sections
- [ ] **F0.8** Stand-up summary: post a 5-bullet validation-sprint outcome to the platform/compliance channel; loop in reviewers from RFC header

**Acceptance:** All 6 ADRs (`0001`–`0006`) merged into `docs/adrs/` with named owner sign-off recorded in the ADR's "Reviewers" header. RFC status updated. **No F1 work begins until F0 closes.**

---

### Phase F1: Config layering refactor

Maps to RFC §15. Two-layer chain: `Settings` → `BaseConfiguration` (Pydantic, layered fields + firm-wide defaults) → `CommonConfiguration` (concrete vendor wiring in `plugins/common/`). Strangler — every existing call to `get_config()` keeps returning a working object.

**Status: complete (PR #22).** Detailed step-level breakdown lives in the F1 sub-plan: [`sentinel-foundations-f1-config-layering.md`](sentinel-foundations-f1-config-layering.md).

What landed:

- [x] **F1.1** Layered fields with RFC §15.5 firm-wide defaults declared on the existing Pydantic `BaseConfiguration` (`config.py`): `investigation_loop_cap=8`, `investigation_timeout_seconds=300`, `confidence_publish_min=0.7`, `confidence_human_review_min=0.4`, `redaction_policy` (default factory → `RedactionPolicy.default()`), `approval_policy` (default factory → `ApprovalPolicy.empty()`), `output_channels`, `runbooks_paths`, `skills_paths`, `tool_modules`, `allowed_tools`, `allowed_skills`, `case_retrieval_*`, `eval_groundedness_min`, `enable_replay_bundle`, `model_id_primary`, `model_id_judge`. `team_id` is a `@property` reading `settings.team_profile`.
- [x] **F1.2** Settings additions per RFC §15.3 (`settings.py`): `team_profile: Literal["sre", "devops", "ace"]`, `litellm_base_url: HttpUrl | None`, `litellm_virtual_key: SecretStr | None`, `langfuse_host: HttpUrl | None`, `langfuse_public_key: SecretStr | None`, `langfuse_secret_key: SecretStr | None`, `otel_collector_endpoint: HttpUrl | None`, `runbooks_root: Path`. Existing fields kept untouched.
- [x] **F1.3** Policy primitives in `src/sentinel/data/policies.py`: `ApprovalPolicy`, `OutputChannel`, `RedactionPolicy` — `attrs.frozen(kw_only=True, slots=True)` with `.default()` and `.empty()` classmethod factories per RFC §15.9. Live in `data/` so `config` composes them without touching `domain`.
- [x] **F1.4** Multi-tenant dispatch via `TEAM_CONFIG_REFS: dict[TeamId, "module:Class"]` registry in `config.py`, resolved at first `get_config()` call via `importlib.import_module`. Only `"sre"` wired; `"devops"`/`"ace"` raise `NotImplementedError` with a registry pointer.
- [x] **F1.5** `src/sentinel/plugins/config.py` → `src/sentinel/plugins/common/config.py` rename in-place. Concrete `CommonConfiguration` lives alongside the future shared substrate (`plugins/common/runbooks/`, `plugins/common/skills/`). Six test imports updated.
- [x] **F1.6** Unit tests in `tests/unit/test_config_layering.py` cover primitive defaults / immutability / placeholder factories, layered field defaults on `BaseConfiguration`, `team_id` derivation from settings, and Settings additions. 17 tests added; full suite (795 unit) green.
- [x] **F1.7** `.env.default` documents the new env vars next to existing groups. `docs/architecture.md` §Configuration describes the two-layer chain and the policy primitives. F1 sub-plan + index updated.

**Dropped from scope (revisit when earned):**

- Separate `BaseConfig` (attrs.frozen) class — collapsed onto the existing Pydantic `BaseConfiguration`.
- Separate `CommonConfig` class in `plugins/common/common.py` — shared defaults live on `BaseConfiguration` itself.
- `SRETeamConfig` and the `plugins/teams/sre/` tree — `team_id` derives from settings; introduce subclasses when behaviour diverges (allowed tools, output channels, runbook paths).
- Import-linter contracts for `plugins/teams/*` — no such tree exists yet.

**Acceptance (met):** `get_config()` returns a `CommonConfiguration` (a `BaseConfiguration` subclass). Existing call sites unchanged. R-OB-2 unblocked (`team_id` available for span tagging). Import-linter contracts pass.

---

### Phase F2: Identity & envelope propagation

Maps to RFC §3.1, R-IN-3, R-IN-4. Mint `request_id` at FastAPI ingress; carry `tenant_id` / `region` / `pii_class` through every span and DB row.

**Status: complete.** Detailed step-level breakdown lives in the F2 sub-plan: [`sentinel-foundations-f2-envelope.md`](sentinel-foundations-f2-envelope.md).

What landed:

- [x] **F2.1** `Envelope` `attrs.frozen(kw_only=True, slots=True)` in `src/sentinel/data/envelope.py` (not `domain/envelope.py` per the parent filemap — placed in `data/` so `config` and `interfaces` compose it without a layer violation, matching the F1 primitives pattern). Six fields per RFC §3.1: `request_id`, `tenant_id`, `cluster_id`, `region`, `pii_class`, `received_at`. `to_span_attributes()` returns the six envelope-owned mandatory OTel attributes; `to_log_context()` returns the structlog binding. Construction enforces tz-aware UTC `received_at`.
- [x] **F2.2** `RequestIdMiddleware` (`BaseHTTPMiddleware` subclass) in `src/sentinel/interfaces/api/middleware.py`. Reuses caller-supplied `X-Request-Id` UUID, mints UUID4 when absent, re-mints on malformed value (with structured warning), sets `request.state.request_id` (UUID object), binds `structlog.contextvars`, attaches `request_id` to the current OTel span, echoes the id back in the response. `try/finally` cleanup unbinds contextvars on exception.
- [x] **F2.3** Middleware wired in `src/sentinel/interfaces/api/app.py` between `bootstrap_otel.instrument_fastapi(app=app)` and the metrics mount, before any `app.include_router(...)`.
- [x] **F2.4** `envelope_factory` module (`src/sentinel/interfaces/webhooks/envelope_factory.py`) composes `Envelope` from PagerDuty / Datadog / Jira / manual payloads. tenant_id precedence: k8s namespace label → service tag → `"unknown"` with structured warning. Tenant slugs sanitised and capped at the k8s namespace limit (63 chars). `BaseConfiguration.envelope_strict_mode: bool = False` (default soft-fail) flips strict-mode to raise `EnvelopeIngressError`, which both routers surface as a 422 with a stable JSON shape (`{"error": "envelope_ingress_missing_tenant_id", "source": ..., "request_id": ...}`). The error carries `source`, `request_id`, and a `missing_tenant_id` flag for machine-readable handling.
- [x] **F2.5** SRE and support pipelines' `State` now require `envelope: Envelope`. Pipeline entry points (`investigate_alert`, `review_ticket`) require `envelope=` kwarg.
- [x] **F2.6** New `run_node_with_envelope` helper in `src/sentinel/interfaces/graphs/_node_helpers.py` sets the six envelope-owned mandatory OTel attributes on every node span (RFC §13.2). The other three mandatory attrs (`prompt_version_sha`, `model_id`, `team_profile`) come from agent invocation context and remain F4 work.
- [x] **F2.7** Same helper binds `envelope.to_log_context()` onto `structlog.contextvars` via `bound_contextvars` (auto-cleans on exception). Every SRE and support node runs through the helper.
- [x] **F2.8** PII redaction in `to_log_context()` swaps `tenant_id` for a 12-char sha256 `tenant_hash` when `pii_class in ("confidential", "mnpi")`. Public predicate `is_redacted_pii_class()` exposes the rule for downstream consumers (redactors, exporters).
- [x] **F2.9** 22 unit tests in `tests/unit/test_envelope.py` (construction, immutability, span-attribute shape, redaction by pii_class, predicate). 9 integration tests in `tests/integration/interfaces/api/test_request_id_propagation.py` covering webhook→response echo, span-attribute landing, structlog contextvars binding, Datadog/Jira variants, strict-mode rejection (422 with no pipeline span), and soft-mode fallback (`tenant_id="unknown"` + warning log). DB-row request_id (F3) and Langfuse span export (F4) deferred per phase scope.

**Auxiliary work delivered alongside F2:**

- Worker (`worker.py`) rehydrates the ingress envelope from queued payload fields (`ingress_request_id`, `pii_class`, `tenant_id`, `cluster_id`, `region`) so the worker leg of the pipeline keeps the same correlation id and PII classification as the ingress leg.
- Replay (`replay.py`), chat (`interfaces/chat/app.py`), and Slack (`interfaces/slack/event_handlers.py`) callers mint per-invocation placeholder envelopes today; F4.5 retires the replay placeholder, chat/Slack stay until those surfaces gain real tenant resolution.
- 13 existing pipeline / functional / eval tests updated to pass the `envelope` kwarg.

**Tech debt captured (post-F2 follow-ups):**

- Hoist `_envelope_ingress_failure_response` from both routers into `envelope_factory` as a public helper (verbatim duplicate today).
- Add `Envelope.to_job_payload()` / `Envelope.from_job_payload()` to centralise queue serialisation; routers and worker currently maintain parallel hand-rolled dicts.
- Add `envelope_factory.envelope_placeholder(*, source, ...)` so chat / Slack / replay placeholder helpers shrink to a one-liner.
- Promote `_UNKNOWN_TENANT` (and a future `_MANUAL_TENANT`) onto `data/envelope.py` as public constants; today the `"unknown"` literal is re-declared in worker, replay, chat, and Slack.
- Drop the now-dead `envelope` kwarg from `instrumented_node_run` (production goes through `run_node_with_envelope` exclusively); fold the OTel-attr setting into the wrapper and rewrite the two helper-tests to drive `run_node_with_envelope` directly.
- `_sanitize_tenant_slug`'s 63-char cap currently only applies to the PagerDuty service-summary path; lift the cap into `_finalise_tenant_id` so k8s namespace, Datadog, and Jira sources honour it too.
- Cache `to_log_context()` / `to_span_attributes()` outputs at construction time once F4 elevates `pii_class` onto the hot path (the per-emit sha256 is dormant in F2's default `pii_class="internal"`).
- Chart pipeline (`interfaces/graphs/chart_generation.py`) was not updated with envelope propagation. Off the F2 critical path; revisit when chart_generation gets multi-tenant traffic.
- Hoist the `recorded_spans` test fixture into `tests/conftest.py` (duplicated between `test_middleware.py` and `test_request_id_propagation.py`).

**Acceptance (met):** Webhook POST generates UUID `request_id` echoed in response header, OTel spans, and structlog contextvars. `pii_class` controls log redaction. Strict-mode flag flips soft-fail to a 422 with a stable error shape. R-IN-3 met (envelope minted at every webhook stage; foundations warn-and-continue, production deploys flip strict). DB-row propagation (F3) and Langfuse span export (F4) extend the chain in subsequent phases.

---

### Phase F3: DB schema gap-fill (8 canonical tables)

Maps to RFC §12.3. Add the 4 missing canonical tables; tighten 4 existing tables. All migrations reversible.

- [x] **F3.1** Audit existing schema vs RFC §12.3. Confirmed during F3.2/F3.3 dispatch: `data/sql/investigations.py::InvestigationRecord` ≈ RFC `investigation` (extend in F3.7), `data/sql/audit.py::AuditLogRecord` ≈ RFC `audit_log` (extend with WORM in F3.6), `data/sql/tracing.py::AgentCallRecord` ≈ RFC `tool_call` (extend in F3.8). **No `FindingRecord` table — findings live as JSONB `findings_json` on `InvestigationRecord`; foundations keeps that shape (§12.3.5 dedicated `finding` table is wk5+ work).** Plan filemap referenced pre-restructure paths (`data/audit_models.py`, `data/models.py`, `data/tracing_models.py`); actual paths are `data/sql/<name>.py` after the 2418e8a/6c41605 split. Column-delta docstrings landed at the top of each new migration in F3.2+ commits
- [x] **F3.2** ✅ Created `src/sentinel/data/sql/alert_requests.py` (path corrected for the post-restructure layout) with `AlertRequestRecord` SQLModel per RFC §12.3.1: PK `request_id: UUID`, `tenant_id: str (indexed)`, `received_at: datetime UTC`, `provider: Literal["pagerduty", "datadog", "alertmanager"]`, `alert_id: str`, `severity: str`, `redacted_annotations: JSONB`, `dedup_status: Literal["new", "duplicate"]`. Migration `alembic/versions/008_alert_request_table.py` (down_revision="007"). Composite indexes `(tenant_id, received_at desc)` and `(provider, alert_id)` plus single-col `tenant_id` index. Commit `62eb3ce`
- [x] **F3.3** ✅ Created `src/sentinel/data/sql/runbooks.py` (path corrected) with `RunbookMatchRecord` per RFC §12.3.2: PK `match_id: UUID`, FK `request_id` → `alert_request.request_id` (constraint `fk_runbook_match_alert_request`), `runbook_id: str`, `runbook_version_sha: str (max_length=32)`, `match_method: Literal["tag", "rag", "generic_fallback"]`, `match_confidence: float`, `matched_at: datetime UTC`. Migration `009_runbook_match_table.py` (down_revision="008"). Commit `892c7b7`
- [ ] **F3.4** Create `src/sentinel/data/task_models.py` with `InvestigationTaskRecord` + `TaskStatusChangeRecord` per RFC §12.3.7: tasks table (PK `task_id`, FK `investigation_id`, `task_text`, `created_at`, `completed_at`, `evidence_refs: JSONB`); task_status_change table (PK, FK task_id, `from_status`, `to_status`, `at`, `reason`). Migration `010_investigation_task_table.py`
- [ ] **F3.5** Create `src/sentinel/data/quality_models.py` with `QualityVerdictRecord` per RFC §12.3.8: PK `verdict_id`, FK `investigation_id`, `groundedness_pass: bool`, `evidence_ref_count: int`, `confidence_score: float`, `verdict_reason: str`, `assessed_at: datetime UTC`. Plus `ApprovalRecord` (PK, FK verdict_id, `approver`, `decision`, `decided_at`). Migration `011_quality_verdict_table.py`
- [ ] **F3.6** Extend `src/sentinel/data/audit_models.AuditLogRecord` per RFC §12.3.10: add columns `request_id: UUID (indexed)`, `prev_hash: str`, `row_hash: str (computed via trigger)`. Add Postgres trigger blocking UPDATE/DELETE on `audit_log` rows (a Postgres trigger is the simplest WORM enforcement that doesn't require a separate role; full role-based separation is a wk5+ followup per the F0.6 ADR). Migration `012_audit_log_worm_constraints.py`
- [ ] **F3.7** Extend `src/sentinel/data/models.InvestigationRecord` toward RFC `investigation` shape per RFC §12.3.4: ADD columns `request_id: UUID (FK alert_request)`, `runbook_match_id: UUID | None (FK)`, `model_id_primary: str`, `iteration_count: int`, `terminated_reason: str | None`, `loop_cap_hit: bool`. Migration `013_extend_investigation_tool_call.py` (combined with F3.8)
- [ ] **F3.8** Extend `src/sentinel/data/tracing_models.AgentCallRecord` toward RFC `tool_call` shape per RFC §12.3.6: ADD columns `tool_name: str`, `capability_token: str | None`, `evidence_object_ids: JSONB`, `succeeded: bool`, `tenant_id: str (indexed)`. Same migration `013_extend_investigation_tool_call.py`
- [ ] **F3.9** Update `src/sentinel/data/database.py` exports + `__init__.py` so all new SQLModel tables are registered for Alembic autogeneration. `just build-migration "verify schema"` should produce an empty migration (idempotency check)
- [ ] **F3.10** Run `just run-db-migrations` against fresh DB; rollback test via `alembic downgrade base`; confirm reversibility on each new migration. Re-apply, confirm idempotency
- [ ] **F3.11** Integration test `tests/integration/test_8_canonical_tables.py`: synthetic alert flow writes one row to each of the 8 canonical tables; FK integrity holds; WORM trigger rejects an `UPDATE audit_log` and `DELETE FROM audit_log`

**Acceptance:** All 8 RFC-canonical tables exist with correct columns, indexes, and FKs. `just test-integration` green. Migrations reversible. WORM trigger blocks audit_log mutations.

---

### Phase F4: OTEL → Langfuse → replay-bundle triple (the single failure mode)

Maps to RFC §13 + §3.8 + R-OB-1, R-OB-2, R-AG-4. **The phase that protects every other phase.** Bed this in or §14.7 happens.

- [ ] **F4.1** Audit current OTel spans for the mandatory attribute set per RFC §13.2: `request_id`, `tenant_id`, `pii_class`, `prompt_version_sha`, `model_id`, `team_profile`. Add missing setters in `instrumented_node_run()` (F2.6 covers envelope-derived attrs; this step adds `prompt_version_sha` and `model_id` from the agent invocation context, plus `team_profile` from `get_config().team_id`)
- [ ] **F4.2** Add a custom OTel span processor `MandatoryAttributesValidator` in `src/sentinel/utils/langfuse_export.py`: on `on_end`, check the mandatory set; if missing, emit a structured warning log AND attach a `_validation_failed=True` span attribute. Do not drop the span (we want incomplete spans visible in Langfuse for debugging). R-OB-2's "exporter rejects spans missing attributes" is met in spirit — incomplete spans are flagged, not silenced
- [ ] **F4.3** Wire Langfuse OTLP exporter in `src/sentinel/bootstrap_otel.py`. Use `logfire.OTLPSpanExporter` pointed at `f"{settings.langfuse_host}/api/public/otel/v1/traces"` with Basic Auth header `base64(public_key:secret_key)`. `send_to_logfire=False`. RFC §13.4 gives the contract. Fallback when `langfuse_host` not set: keep current console exporter
- [ ] **F4.4** Validate end-to-end: synthetic alert via `just run-api` + `curl -X POST /webhooks/...` → trace appears in self-hosted Langfuse with all 6 mandatory attributes visible. Capture screenshot in PR description for review evidence
- [ ] **F4.5** Extend the replay machinery from PR #15. New file `src/sentinel/utils/replay_bundle.py` defines `ReplayBundle` `attrs.frozen` per RFC §3.8: `envelope`, `alert_payload`, `runbook_id`, `runbook_version_sha`, `tool_io: tuple[ToolIOEntry, ...]`, `llm_io: tuple[LLMIOEntry, ...]`, `final_outputs`, `bundle_sha`. `ToolIOEntry` captures `tool_name`, `inputs`, `outputs`, `evidence_object_id`, `at`. Existing `utils/replay.py` becomes a thin shim importing from `replay_bundle`
- [ ] **F4.6** Hook tool I/O capture into the existing toolset wrapper. In `plugins/toolsets/_runtime.py` (create if absent — see if PR #15 already added a wrapper), every tool call emits a `ToolIOEntry` to the active replay context (a `ContextVar[ReplayBundleBuilder]`). Builder flushes on pipeline `End`
- [ ] **F4.7** Implement `python -m sentinel.replay <request_id>` CLI in `src/sentinel/replay_cli.py`. Reads `ReplayBundle` from DB+S3 (use existing `domain/audit/` query helpers); replays the pipeline against recorded LLM/tool responses by injecting a `RecordedTransport` that intercepts LLM and tool calls and returns recorded outputs; emits new outputs; diffs against original `final_outputs` and exits non-zero on mismatch
- [ ] **F4.8** Determinism integration test `tests/integration/test_replay_determinism.py`. Fixture: a recorded ReplayBundle for a synthetic crashloop investigation. Test: run replay 30 times, assert identical `final_outputs` across all 30 runs (R-AG-4 says 100; we do 30 in foundations CI to keep wall time bounded; expand to 100 in nightly job in week 5 plan). Mark as `slow` test marker so it's skippable in `just test`
- [ ] **F4.9** Document Langfuse + replay setup in `docs/architecture.md` §Observability + add a §Replay subsection. Include the §13.2 mandatory attribute table

**Acceptance:** Synthetic alert produces a Langfuse trace tree showing all 6 mandatory attributes. Replay reproduces output bit-for-bit on 30 consecutive runs. R-OB-1 (proxy as chokepoint — comes online in F5), R-OB-2 (mandatory attrs validated), R-AG-4 (replay determinism) all met. **§14.7 failure mode neutralised.**

---

### Phase F5: LiteLLM proxy migration + orchestration framework decision

Maps to RFC §2.4 (proxy migration) + §15.14 (orchestration framework re-eval). Two independent decisions converge in this phase.

- [ ] **F5.1** Author `docs/adrs/0007-orchestration-framework.md` — Pydantic Graph vs LangGraph. Comparison criteria: replay determinism (LangGraph checkpoint vs PR #15 bundle, post-F4), framework swap cost (Pydantic Graph is current; ~5 days to migrate), velocity tradeoff. **Default position: stay on Pydantic Graph for foundations**, revisit at month 3 once F4 replay determinism is proven in production traffic. Reasoning: a working bundle-based replay > a theoretical checkpoint-based one; framework swap mid-foundations would block F6/F7/F8
- [ ] **F5.2** Confirm `litellm_base_url` + `litellm_virtual_key` already in `Settings` (added in F1.2). Update LiteLLM SDK client construction sites: `grep -rn "litellm.completion\|litellm.acompletion" src/` — wrap each in a thin helper `domain/llm/litellm_proxy.py` that pulls `base_url` + `api_key` from `get_config().settings` and passes them through. PydanticAI agent factories use the `litellm:` model prefix and pick up `base_url`/`api_key` via the `Model` constructor's kwargs
- [ ] **F5.3** Update each PydanticAI agent factory (`alert_classifier.py`, `root_cause_analyser.py`, `k8s_investigator.py`, `ticket_reviewer.py`, `response_drafter.py`) to construct the `Model` with proxy `base_url` + virtual key when `litellm_base_url` is set. Pattern (per RFC §2.4 example):
    ```
    Model(
        f"litellm:{model_name}",
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_virtual_key.get_secret_value(),
    )
    ```
- [ ] **F5.4** Local-dev fallback: if `litellm_base_url` is `None`, behave as today (in-process LiteLLM SDK with provider keys). Keeps `just run-api` working without proxy. Add a structured-log warning at startup: `"litellm_proxy_disabled"` when fallback path active
- [ ] **F5.5** Integration test `tests/integration/test_litellm_proxy.py`: spin up a mock LiteLLM proxy via `pytest-httpx` (or a docker-compose litellm container if already in `compose.yaml`); assert outbound LLM calls flow through proxy URL with virtual key in the `Authorization` header; response intact. Test path: fixture POST → pipeline → mock-proxy hit verified
- [ ] **F5.6** Update `.env.default` documenting `LITELLM_BASE_URL`, `LITELLM_VIRTUAL_KEY` with comments pointing to RFC §2.4. Update `docs/architecture.md` §LLM with the proxy-vs-SDK distinction
- [ ] **F5.7** Acceptance test for R-OB-1: with `litellm_base_url` set and an iptables rule blocking direct egress to provider endpoints (in CI's docker network), confirm the LLM call still succeeds via proxy. Document the test setup; this is the foundations slice of R-OB-1; the full network-policy enforcement comes in the wk5 Helm work

**Acceptance:** All LLM calls route through the configurable LiteLLM proxy URL when set. Local dev still works without proxy via fallback. Pydantic Graph stays for foundations per ADR 0007. R-OB-1 met (proxy chokepoint at app layer — network-policy enforcement is Helm/wk5 concern).

---

### Phase F6: Runbook catalog + tag-based matcher

Maps to RFC §4 + R-RB-1, R-RB-2. New runbook envelope coexists with existing skills system (RFC §4.5 says both, at different layers).

- [ ] **F6.1** Define `src/sentinel/domain/runbooks/models.py`. Frozen attrs: `RunbookMetadata(runbook_id: str, version_sha: str, tags: tuple[RunbookTag, ...], owner: str, severity_filter: tuple[str, ...])`, `RunbookTag(key: str, value: str)`, `ToolSpec(name: str, max_calls: int)`, `CheckSpec(name: str, kind: Literal["pre", "post"], expression: str)`, `TestSpec(name: str, fixture_path: str, expected_runbook_match: bool)`, `Runbook(metadata, body, tools, checks, tests, source_dir)`. `version_sha` computed via `sha256` over the four files' bytes
- [ ] **F6.2** Implement `src/sentinel/domain/runbooks/loader.py`. `load_runbook(directory: Path) -> Runbook` reads `RUNBOOK.md` (frontmatter + body via `python-frontmatter`), `tools.yaml`, `checks.yaml`, `tests.yaml` (PyYAML); computes `version_sha`; caches via `lru_cache(maxsize=128)`. `discover_runbooks(roots: tuple[Path, ...]) -> Mapping[str, Runbook]` iterates roots and assembles a mapping keyed by `runbook_id`. First-wins semantics on duplicate IDs across paths (per RFC §15.10.5), with structured-log warning on duplicates
- [ ] **F6.3** Pre-commit hook `scripts/compute_runbook_shas.py`: walks runbook roots, computes `version_sha` for each, writes back into the frontmatter `version_sha` field if absent or stale. Wire into `.pre-commit-config.yaml`. R-RB-1 acceptance criterion: "pre-commit hook computes content_sha and writes to frontmatter"
- [ ] **F6.4** Implement `src/sentinel/domain/runbooks/matcher.py`. `match_runbook(*, alert_labels: Mapping[str, str], runbooks: Mapping[str, Runbook]) -> RunbookMatch | None`. Algorithm: score = number of `RunbookTag(key, value)` entries that exactly match `alert_labels[key] == value`. Pick highest-score, break ties by alphabetical `runbook_id` for determinism. Threshold: `score >= 2` to match (avoid trivial alertname-only matches). RAG fallback (R-RB-3) deferred to month 3 per scope
- [ ] **F6.5** Author reference runbook `src/sentinel/plugins/teams/sre/runbooks/k8s-crashloop/`. Full quartet:
  - `RUNBOOK.md` with YAML frontmatter (`runbook_id: k8s-crashloop`, `version_sha: ` placeholder, `tags: [{key: alertname, value: KubePodCrashLooping}, {key: resource_kind, value: Pod}]`, `owner: sre-platform`, `severity_filter: [P1, P2, P3]`) + a markdown body following RFC §4.2 structure (Trigger / Hypotheses / Steps / Confirm / Remediate-suggestion)
  - `tools.yaml`: `k8s_describe_pod`, `k8s_get_events`, `k8s_get_pod_logs`, `prom_query_range` with `max_calls`
  - `checks.yaml`: groundedness rules (every Finding has ≥1 evidence_ref; every evidence_ref must point at a tool_call within the same investigation)
  - `tests.yaml`: 3 golden cases (matcher fixtures: this alert should match, this alert should not, this alert should also match — covers R-RB-2's "50 permutations" via parameterised tests, foundations does 10)
- [ ] **F6.6** Add new pipeline node `MatchRunbook` in `src/sentinel/interfaces/graphs/investigation.py`. Position: after `ClassifyAlert`, before `InvestigateWithHolmes` (or `K8sInvestigator` depending on backend). Reads `state.envelope` + `state.alert`; calls `matcher.match_runbook`; writes `runbook_match` row from F3.3 via existing `domain/audit/` writer pattern; sets `state.runbook = matched_runbook`
- [ ] **F6.7** Update `src/sentinel/interfaces/graphs/agents/k8s_investigator.py` (and `holmes_adapter` and `kagent_adapter` paths) to receive the matched `Runbook` as a Dependency. Agent system prompt (Jinja2 template) gains a conditional block `{% if runbook %}{{ runbook.body }}{% endif %}` rendering the matched runbook body. Other agents (alert_classifier, root_cause_analyser) unchanged
- [ ] **F6.8** Unit tests `tests/unit/test_runbook_loader.py` (fixture directory load, version_sha stability, frontmatter required fields), `tests/unit/test_runbook_matcher.py` (10+ alert-label permutations matching expected runbook IDs, deterministic ties, no-match returns None)
- [ ] **F6.9** Integration test: end-to-end synthetic crashloop alert webhook → `MatchRunbook` writes `runbook_match` row → `K8sInvestigator` system prompt contains the runbook body (assert via OTel span attribute capture or by recording the rendered prompt to the `replay_bundle`)

**Acceptance:** R-RB-1 met (`version_sha` computed by pre-commit, present in frontmatter, written to DB on every match). R-RB-2 met (10-permutation deterministic tag match — full 50 in week 5 plan). One reference runbook drives an end-to-end synthetic investigation with the matched body in the agent's prompt.

---

### Phase F7: Capability tokens for tool authorization

Maps to RFC §5.3 + R-TL-3, R-TL-4. Tools authorized only when active runbook lists them; tenant-scoped.

- [ ] **F7.1** Define `src/sentinel/domain/tools/capabilities.py`. Frozen attrs `CapabilityToken(runbook_id: str, tool_name: str, tenant_id: str, granted_at: datetime)`. Function `validate_tool_call(*, runbook: Runbook, tool_name: str, tenant_id: str, call_namespace: str | None = None) -> CapabilityToken`. Raises `UnauthorizedToolCallError(tool_name, runbook_id)` if `tool_name not in {t.name for t in runbook.tools}`. Raises `ForbiddenTenantError(tenant_id, call_namespace)` if `call_namespace and call_namespace != tenant_id`
- [ ] **F7.2** Update each toolset (`src/sentinel/plugins/toolsets/kubernetes.py`, `observability.py`, others) so every tool function calls `capabilities.validate_tool_call()` at the top before any vendor call. Token persists onto the `tool_call` row (column added in F3.8). The active runbook is read from PydanticAI `RunContext` (deps) and the active envelope from the same context
- [ ] **F7.3** Wire active runbook + envelope into PydanticAI agent `Dependencies` dataclass for every investigator agent. Pattern (per `sentinel.md` rule — Dependencies as `@dataclasses.dataclass`):
    ```
    @dataclasses.dataclass
    class K8sInvestigatorDeps:
        envelope: Envelope
        runbook: Runbook
        k8s_client: KubernetesClient
        ...
    ```
- [ ] **F7.4** Unit tests `tests/unit/test_capability_tokens.py`: tool not in runbook → `UnauthorizedToolCallError`; cross-tenant call (envelope tenant ≠ tool's `namespace` arg) → `ForbiddenTenantError`; both rejections logged via `logs.log_event("capability_rejection", params={...})`; both write a row to `audit_log` (use existing `domain/audit/` writer)
- [ ] **F7.5** Adversarial integration slice `tests/integration/test_tenant_isolation.py`: foundation slice only (full adversarial suite is week 7). Cases: `k8s_get_pod_logs(namespace="other-pm")` with envelope tenant=`pm-a` → `ForbiddenTenantError` + audit_log row; tool not in runbook (call `prom_query_range` from a runbook that only lists k8s_*) → `UnauthorizedToolCallError` + audit_log row
- [ ] **F7.6** Update `docs/architecture.md` §Capability Tokens with the token shape, rejection types, and audit-log row schema

**Acceptance:** R-TL-3 met (tools outside runbook's `tools.yaml` rejected with structured error + audit_log row). R-TL-4 met at the app layer (cross-tenant rejection; K8s RBAC layer comes in wk5 plan). Both rejection types logged.

---

### Phase F8: Quality gate (deterministic groundedness) + replay determinism CI

Maps to RFC §5.4 + R-QG-1 + R-AG-4 + R-CO-1. Closes the foundations loop.

- [ ] **F8.1** Define `src/sentinel/domain/quality/groundedness.py`. Frozen attrs `GroundednessVerdict(passed: bool, missing_evidence_findings: tuple[str, ...], stale_evidence_refs: tuple[str, ...], reason: str)`. Function `assess_groundedness(*, findings: tuple[Finding, ...], tool_calls: tuple[ToolCallRecord, ...]) -> GroundednessVerdict`. Rules per RFC §5.4: every `Finding` has ≥1 entry in `evidence_refs`; every `evidence_ref` matches a recorded `tool_call.evidence_object_id` in this investigation
- [ ] **F8.2** Add new pipeline node `AssessQuality` in `src/sentinel/interfaces/graphs/investigation.py`. Position: after `AnalyseRootCause`, before `DetermineConfidence`. Reads `state.findings` + `state.tool_calls`; runs `assess_groundedness`; writes `quality_verdict` row from F3.5; sets `state.quality_verdict`
- [ ] **F8.3** Update `DetermineConfidence` to consume `state.quality_verdict`. When `groundedness_pass=False`, return `End(failure_mode="ungrounded", verdict_reason=verdict.reason)` instead of proceeding to approval gate. Existing approval-gate logic untouched for grounded paths
- [ ] **F8.4** Wire `audit_log` writes for every state transition (R-CO-1). New file `src/sentinel/application/audit.py` (if not present — check via `find src -name audit.py`) exposes `record_transition(*, request_id, from_state, to_state, reason)`. Call from each pipeline node's exit. Transitions: `received → matched → investigated → quality_assessed → confidence_scored → published_or_blocked`. Each row links via `prev_hash` to the previous row in the same `request_id` chain
- [ ] **F8.5** Unit tests `tests/unit/test_groundedness.py`: finding without evidence_ref → fail; finding with non-matching evidence_ref → fail; clean run (every finding linked to a recorded tool_call) → pass; empty findings list → pass with reason "no findings to ground" (so we don't fail trivially when an investigation produces nothing)
- [ ] **F8.6** Wire replay determinism check into CI. Add `tests/integration/test_replay_determinism.py` (built in F4.8) to `just test-integration`. Add a GitHub Actions job (or update existing CI workflow) that runs `just test-integration` on every PR; failure blocks merge. The daily replay-diff job (R-OB-5) reuses this test once Helm is deployed in wk5 plan
- [ ] **F8.7** Update `docs/prd.md` acceptance-criteria checkboxes — tick all foundations-met R-* items: R-IN-3, R-IN-4 (partial — foundations is soft-fail, hard-fail in wk5), R-RB-1, R-RB-2, R-TL-3, R-TL-4 (app-layer slice), R-QG-1, R-AG-4 (30-run slice), R-OB-1 (app-layer slice), R-OB-2, R-CO-1
- [ ] **F8.8** Update `docs/architecture.md` to document the foundations layer cake; add the Phase F0–F8 timeline; update the SRE pipeline diagram to show `MatchRunbook` and `AssessQuality` nodes
- [ ] **F8.9** Run `/update-docs` per CLAUDE.md workflow — diffs the foundations commits against `docs/prd.md` and confirms checkbox updates are accurate

**Acceptance:** R-QG-1 met (gate rejects fixture with empty `evidence_refs`). R-AG-4 met (replay reproduces output bit-for-bit on 30 consecutive CI runs). R-CO-1 met (`audit_log` write for every state transition with `prev_hash` chain). All foundations PRD checkboxes ticked.

---

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-25 | Initial draft | RFC-001 v0.4 ratified; foundations defined per evolve-in-place strategy |
| 2026-04-25 | F1 pivot recorded: collapsed 4-layer attrs chain (`Settings → BaseConfig → CommonConfig → SRETeamConfig`) to two-layer Pydantic chain (`Settings → BaseConfiguration → CommonConfiguration`); multi-tenant via `Settings.team_profile` + `TEAM_CONFIG_REFS` registry; `SRETeamConfig` deferred until team behaviour diverges. Phase F1 marked complete (PR #22). | Pydantic `BaseModel` is the project's existing config contract; collapsing to one type avoided parallel attrs/Pydantic types and earned the right to keep team subclasses for when divergent behaviour actually exists. |
| 2026-04-26 | F3 path correction: new SQLModel files land in `data/sql/<name>.py` (not `data/<name>_models.py`) after the 2418e8a `data/` restructure. F3.2 + F3.3 landed on `feat/sentinel-foundations-f3-db-schema` with `tenant_id`-prefixed composite indexes and an explicit FK constraint name on `runbook_match`. F3.1 closed: no dedicated `finding` table in foundations — findings stay JSONB on `InvestigationRecord` until the wk5+ `§12.3.5 finding` plan. | Plan filemap predated the data-layer split; new tables follow the actual repo convention. The dedicated `finding` table is out of foundations scope per the plan's §3.5 cut. |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up plans (week 5+ of RFC §14, and beyond)

- `sentinel-hedgefund-deployment.md` — Helm chart finalisation, real K8s cluster wiring, OTEL collector, network policies, Pod Security (RFC §6, §14 wks 5–8)
- `info-barriers.md` — 5-layer info barrier layers 3–5: LiteLLM tenant routing, full redactor with LLM judge, Postgres RLS by tenant_id (RFC §5.7, month 3+)
- `case-history-retrieval.md` — pgvector + BM25 case-history retrieval (RFC §3.3.1, needs ≥100 confirmed investigations)
- `langgraph-migration.md` — orchestration framework migration if ADR 0007 flips at month 3 (RFC §15.14)
- `adversarial-fixtures.md` — adversarial test suite for cross-PM injection, prompt injection (RFC §5.6 + §10.6, wk7)
- `audit-worm-archive.md` — WORM archive job for `audit_log` 7-year retention (RFC §12.3.10, wk5+)
- `devops-team-profile.md` and `ace-team-profile.md` — DevOps and ACE profiles (RFC §1.4, months 4–6)
- `llm-judge-quality-gate.md` — LLM judge for redactor + quality gate (RFC §5.4, month 3)

### Tech debt to revisit
- F2.4 `envelope_strict_mode = False` — flip to True in wk5 plan once webhook tenant_id derivation is robust across all alert sources
- F4.2 mandatory-attribute validator flags but doesn't drop incomplete spans — tighten to drop in production once shadow mode confirms no false positives
- F6.4 RAG fallback for runbook matcher — month 3 once tag-based coverage data is in
- F8.6 30-run determinism CI — expand to 100-run nightly once Helm landed
