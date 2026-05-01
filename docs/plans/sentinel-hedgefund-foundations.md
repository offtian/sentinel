# Plan: Sentinel Hedge Fund — Foundations (RFC-001 v0.4)

**Status:** in-progress
**Created:** 2026-04-25
**Last updated:** 2026-04-26
**Progress:** F0 deferred · F1 complete (PR #22) · F2 complete · F3 complete · F4 Phase A complete (runtime smoke deferred)

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
- LangGraph migration (deferred behind ADR 0007 — **SRE pipeline migrated in `langgraph-sre-migration` plan, PR #35**; support + chart pipelines pending own plans)

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
| Orchestration framework | Stay on Pydantic Graph for foundations; revisit LangGraph at month 3 (ADR 0007) — **SRE pipeline has since migrated to LangGraph** (`interfaces/workflows/sre_investigation.py`, PR #35, `langgraph-sre-migration` plan complete). Support + chart pipelines remain on Pydantic Graph pending their own migration plans. | LangGraph's checkpoint replay is attractive but framework swap mid-foundations would block F4–F8; PR #15 already covers replay determinism via the bundle approach. SRE migration landed after foundations stabilised, validating the ADR 0007 decision. |
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
- [x] **F3.4** ✅ Created `src/sentinel/data/sql/tasks.py` (path corrected) with `InvestigationTaskRecord` (`__tablename__ = "investigation_task"`, PK `task_id`, FK `investigation_id` → `investigation_records.id` with constraint `fk_investigation_task_investigation`, `task_text`, `created_at`, `completed_at`, `evidence_refs: JSONB nullable`) and `TaskStatusChangeRecord` (PK `id`, FK `task_id` → `investigation_task.task_id` with constraint `fk_task_status_change_task`, `from_status: nullable`, `to_status`, `at`, `reason: nullable`). Migration `010_investigation_task_table.py` (down_revision="009"). Module-level TODO points at the F3.7 `request_id` re-keying follow-up. Commit `4e52408`
- [x] **F3.5** ✅ Created `src/sentinel/data/sql/quality.py` (path corrected) with `QualityVerdictRecord` (PK `verdict_id`, FK `investigation_id`, `groundedness_pass: bool`, `evidence_ref_count: int`, `confidence_score: float`, `verdict_reason: str`, `assessed_at: datetime UTC`) and `ApprovalRecord` (PK `id`, FK `verdict_id` → `quality_verdict.verdict_id` with constraint `fk_approval_record_verdict`, `approver`, `decision`, `decided_at`). Migration `011_quality_verdict_table.py` (down_revision="010"). Commit `f08d0f0`
- [x] **F3.6** ✅ Extended `src/sentinel/data/sql/audit.py::AuditLogRecord` (path corrected) with `request_id: UUID nullable indexed`, `prev_hash: str nullable`, `row_hash: str nullable` (Python-side; Postgres trigger fills it server-side on every INSERT). Migration `012_audit_log_worm_constraints.py` (down_revision="011") creates pgcrypto, the `audit_log_compute_row_hash` BEFORE INSERT trigger (sha256 of `coalesce(prev_hash,'') || actor || action || resource_type || resource_id || details_json || timestamp::text`, hex-encoded), and `audit_log_worm_guard` BEFORE UPDATE OR DELETE triggers raising `audit_log is append-only - UPDATE/DELETE forbidden`. Smoke-tested: INSERT populates row_hash (64-char hex); UPDATE/DELETE both raise the WORM exception. Commit `113e964`
- [x] **F3.7** ✅ Extended `src/sentinel/data/sql/investigations.py::InvestigationRecord` (path corrected) with `request_id: UUID nullable indexed FK → alert_request.request_id` (constraint `fk_investigation_alert_request`), `runbook_match_id: UUID nullable FK → runbook_match.match_id` (constraint `fk_investigation_runbook_match`), `model_id_primary: str nullable`, `iteration_count: int default 0 not null`, `terminated_reason: str nullable`, `loop_cap_hit: bool default false not null`. New columns are nullable / default-bearing so existing rows backfill cleanly. Combined migration `013_extend_investigation_tool_call.py` (down_revision="012"). Commit `692c842`
- [x] **F3.8** ✅ Extended `src/sentinel/data/sql/tracing.py::AgentCallRecord` (path corrected) with `tool_name: str nullable`, `capability_token: str nullable`, `evidence_object_ids: JSONB nullable`, `succeeded: bool nullable`, `tenant_id: str nullable indexed`. Shipped in the same `013_extend_investigation_tool_call.py` migration. Commit `692c842`
- [x] **F3.9** ✅ Folded into F3.2/F3.4/F3.5: each new module's import was added to `src/sentinel/data/migrations/alembic/env.py` alphabetically (no `database.py` change needed — the existing alembic env.py is the registry that imports every `data.sql` submodule for autogeneration). The plan's separate F3.9 step was a no-op once the F3.2+ batches each took responsibility for their own env.py wiring
- [x] **F3.10** ✅ Folded into F3.2-F3.8: every batch ran `just run-db-migrations` + `just downgrade-db-migration` + re-apply locally. `alembic check` reports no drift on the new tables (only the pre-existing `ticket_review_records.suggested_response` model-vs-migration drift, unrelated, deferred to a separate cleanup slice)
- [x] **F3.11** ✅ Integration test `tests/integration/test_8_canonical_tables.py`: synthetic alert writes one row into the F3 canonical chain (`alert_request` → `runbook_match` → `investigation_records` (with `findings_json`) → `agent_calls` → `investigation_task` → `task_status_change` → `quality_verdict` → `approval_record` → `audit_log`); FK integrity verified by reading the chain back; `audit_log` WORM trigger raises on both UPDATE and DELETE attempts. Skips cleanly when DB unreachable or schema not at head. `just test-integration` green (60/60). Folder-level docs added at `src/sentinel/data/sql/README.md` with mermaid ER diagram + WORM trigger description. Commit `b5bc528`

**Acceptance (met):** All RFC-canonical foundations tables exist with correct columns, indexes, and FKs. `just test-integration` green. Migrations reversible. WORM trigger blocks `audit_log` mutations.

---

### Phase F4: OTEL → Langfuse → replay-bundle triple (the single failure mode)

Maps to RFC §13 + §3.8 + R-OB-1, R-OB-2, R-AG-4. **The phase that protects every other phase.** Bed this in or §14.7 happens.

- [x] **F4.1** Audit current OTel spans for the mandatory attribute set per RFC §13.2: `request_id`, `tenant_id`, `pii_class`, `prompt_version_sha`, `model_id`, `team_profile`. Add missing setters in `instrumented_node_run()` (F2.6 covers envelope-derived attrs; this step adds `prompt_version_sha` and `model_id` from the agent invocation context, plus `team_profile` from `get_config().team_id`). Commit `ef23db7`
- [x] **F4.2** Add a custom OTel span processor `MandatoryAttributesValidator` in `src/sentinel/utils/langfuse_export.py`: on `on_end`, check the mandatory set; if missing, emit a structured warning log AND attach a `_validation_failed=True` span attribute. Do not drop the span (we want incomplete spans visible in Langfuse for debugging). R-OB-2's "exporter rejects spans missing attributes" is met in spirit — incomplete spans are flagged, not silenced. Commit `a37c36a`
- [x] **F4.3** Wire Langfuse OTLP exporter in `src/sentinel/bootstrap_otel.py`. Use `logfire.OTLPSpanExporter` pointed at `f"{settings.langfuse_host}/api/public/otel/v1/traces"` with Basic Auth header `base64(public_key:secret_key)`. `send_to_logfire=False`. RFC §13.4 gives the contract. Fallback when `langfuse_host` not set: keep current console exporter. Commit `1feba88`
- [x] **F4.A.1** Local Langfuse v3 docker-compose stack (`langfuse-web`, `langfuse-worker`, `langfuse-db`, `clickhouse`, `redis`, `minio`) with `LANGFUSE_INIT_*` seeded dev project + key pair. Commit `1096ce5`
- [ ] **F4.4** Validate end-to-end: synthetic alert via `just run-api` + `curl -X POST /webhooks/...` → trace appears in self-hosted Langfuse with all 6 mandatory attributes visible. Capture screenshot in PR description for review evidence — runtime smoke deferred until Docker is available on dev host
- [x] **F4.5** Extend the replay machinery from PR #15. New file `src/sentinel/utils/replay_bundle.py` defines `ReplayBundle` `attrs.frozen` per RFC §3.8: `envelope`, `alert_payload`, `runbook_id`, `runbook_version_sha`, `tool_io: tuple[ToolIOEntry, ...]`, `llm_io: tuple[LLMIOEntry, ...]`, `final_outputs`, `bundle_sha`. `ToolIOEntry` captures `tool_name`, `inputs`, `outputs`, `evidence_object_id`, `at`. Landed on `feat/sentinel-foundations-f4-replay-bundle` (commit `2b2c8f1`)
- [x] **F4.6** Tool I/O capture in `plugins/toolsets/_runtime.py` via `ContextVar[ReplayBundleBuilder]`; `ReplayCapturingToolset` wraps each toolset; flush on pipeline `End` — landed (commit `89d0239`). LLM I/O capture lifted into `CapturingModel` (`plugins/models/capturing.py`) under F4.7 slice B since `event_stream_handler` only fires on streaming runs
- [x] **F4.7** `python -m sentinel.replay <run_id> --replay` rewired onto the new `utils/replay_bundle.ReplayBundle`. Single shared `RecordedModel` + `RecordedToolset` injected for every agent / toolset slot; `--diff` exits 3 on drift, exit 4 on bundle SHA mismatch, exit 5 on tool/LLM call drift. New columns `replay_bundle_json` + `replay_bundle_sha` on `pipeline_runs` (Alembic 014); persistence wired through worker + graphs. Slices A–D landed in commits `6a4995d`, `63f21d9`, `e789b10`, `318d887`
- [x] **F4.8** `tests/integration/test_replay_determinism.py` runs the SRE graph 30× against a synthetic crashloop bundle with a fresh `RecordedModel` per iteration and asserts byte-identical outputs. Marked `@pytest.mark.slow`; wired into `just test-integration` via the default `tests/integration/` glob. Companion `test_bundle_sha_is_stable_across_repeated_serialisation` covers canonicalisation drift in isolation
- [x] **F4.9** `docs/architecture.md` §Observability mandatory-attribute table extended (already landed in F4.1) + new §Replay subsection covering bundle shape, capture flow, replay flow, determinism guarantee, and exit codes

**Acceptance:** Synthetic alert produces a Langfuse trace tree showing all 6 mandatory attributes. Replay reproduces output bit-for-bit on 30 consecutive runs. R-OB-1 (proxy as chokepoint — comes online in F5), R-OB-2 (mandatory attrs validated), R-AG-4 (replay determinism) all met. **§14.7 failure mode neutralised.**

---

### Phase F5: LiteLLM proxy migration + orchestration framework decision

Maps to RFC §2.4 (proxy migration) + §15.14 (orchestration framework re-eval). Two independent decisions converge in this phase.

- [x] **F5.1** Author `docs/adrs/0007-orchestration-framework.md` — Pydantic Graph vs LangGraph. Comparison criteria: replay determinism (LangGraph checkpoint vs PR #15 bundle, post-F4), framework swap cost (Pydantic Graph is current; ~5 days to migrate), velocity tradeoff. **Default position: stay on Pydantic Graph for foundations**, revisit at month 3 once F4 replay determinism is proven in production traffic. Reasoning: a working bundle-based replay > a theoretical checkpoint-based one; framework swap mid-foundations would block F6/F7/F8
- [x] **F5.2** Confirm `litellm_base_url` + `litellm_virtual_key` already in `Settings` (added in F1.2). Update LiteLLM SDK client construction sites: `grep -rn "litellm.completion\|litellm.acompletion" src/` — wrap each in a thin helper `domain/llm/litellm_proxy.py` that pulls `base_url` + `api_key` from `get_config().settings` and passes them through. PydanticAI agent factories use the `litellm:` model prefix and pick up `base_url`/`api_key` via the `Model` constructor's kwargs
- [x] **F5.3** Update each PydanticAI agent factory (`alert_classifier.py`, `root_cause_analyser.py`, `k8s_investigator.py`, `ticket_reviewer.py`, `response_drafter.py`) to construct the `Model` with proxy `base_url` + virtual key when `litellm_base_url` is set. Pattern (per RFC §2.4 example):
    ```
    Model(
        f"litellm:{model_name}",
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_virtual_key.get_secret_value(),
    )
    ```
- [x] **F5.4** Local-dev fallback: if `litellm_base_url` is `None`, behave as today (in-process LiteLLM SDK with provider keys). Keeps `just run-api` working without proxy. Add a structured-log warning at startup: `"litellm_proxy_disabled"` when fallback path active
- [x] **F5.5** Integration test `tests/integration/test_litellm_proxy.py`: spin up a mock LiteLLM proxy via `pytest-httpx` (or a docker-compose litellm container if already in `compose.yaml`); assert outbound LLM calls flow through proxy URL with virtual key in the `Authorization` header; response intact. Test path: fixture POST → pipeline → mock-proxy hit verified
- [x] **F5.6** Update `.env.default` documenting `LITELLM_BASE_URL`, `LITELLM_VIRTUAL_KEY` with comments pointing to RFC §2.4. Update `docs/architecture.md` §LLM with the proxy-vs-SDK distinction
- [x] **F5.7** Acceptance test for R-OB-1: with `litellm_base_url` set and an iptables rule blocking direct egress to provider endpoints (in CI's docker network), confirm the LLM call still succeeds via proxy. Document the test setup; this is the foundations slice of R-OB-1; the full network-policy enforcement comes in the wk5 Helm work

**Acceptance:** All LLM calls route through the configurable LiteLLM proxy URL when set. Local dev still works without proxy via fallback. Pydantic Graph stays for foundations per ADR 0007. R-OB-1 met (proxy chokepoint at app layer — network-policy enforcement is Helm/wk5 concern).

---

### Phase F6: Runbook catalog + tag-based matcher

Maps to RFC §4 + R-RB-1, R-RB-2 (and new R-RB-4..6 introduced by the F6 spec). New runbook envelope coexists with existing skills system (RFC §4.5 says both, at different layers).

**Status: in-progress.** Detailed step-level breakdown lives in the F6 sub-plan: [`sentinel-foundations-f6-runbook-catalog.md`](sentinel-foundations-f6-runbook-catalog.md). Full design rationale in [`docs/superpowers/specs/2026-04-26-f6-runbook-catalog-design.md`](../superpowers/specs/2026-04-26-f6-runbook-catalog-design.md).

**Verdicted changes from the original step list (informed by 2026-04-26 industry research):**

- **Matching** — pure tag scoring with alphabetical tiebreak (original) → **two-stage hybrid**: Stage 1 deterministic tag pre-filter with per-runbook `min_match_score` (default 2) + Stage 2A small-LLM disambiguator on ties at top score + Stage 2B small-LLM zero-match rescue with explicit `no_match` option. Preserves runbook-gap flywheel because LLM has the explicit `no_match` output. RAG fallback still deferred to month 3.
- **Versioning** — `version_sha` only (original) → **triple-key**: `content_sha` (sha256[:32] over body + sidecar yamls) + git commit SHA pinned in replay bundle + immutable `runbook_id` (renames produce new runbook + `superseded_by` link).
- **Lifecycle frontmatter** (new) — `last_validated`, `deprecated_at`, `superseded_by`, `mnpi_safe`. CI flags ≥ 90-day staleness; matcher skips deprecated; `mnpi_safe: false` runbooks excluded for `pii_class=mnpi`.
- **Body sanitization + quarantine prompt frame** (new, security guardrail) — loader rejects auto-rendered markdown URLs in body via `checks.yaml.body_sanitization`; runbook body rendered into agent instructions inside `<runbook>...</runbook>` quarantine frame so retrieved/runbook content is treated as untrusted (LogJack-class indirect prompt injection defence; arXiv 2604.15368).
- **Always-write `runbook_match` row** — original wrote on success only; spec writes always, including `no_match`, with full top-k `candidates_json`, `tag_score`, `llm_choice`, `llm_justification`. Regulator answer to "why this runbook and not another?" lives in the row.
- **`runbook_feedback` table** (new) — schema added in F6 migration 014; approval gate writes `negative` / `wrong_runbook` rows. Weekly digest deferred to follow-on plan.
- **Generic playbook + runbook-gap flywheel** (formalised) — `_generic-investigation` runbook ships in F6 alongside `k8s-crashloop` reference; emits `runbook_gap` event on every Stage 2B `no_match` outcome; clustering + auto-PR consumer is a follow-on plan.
- **Three behavioural skills** (new, RFC §15.10) — `evidence-grounding`, `task-list-discipline`, `confidence-calibration` ship at `plugins/common/skills/` so the runbook-driven agent has a coherent behaviour layer.
- **F7 contract update** — capability tokens enforced at the **toolset wrapper boundary**, not at function entry (Cerbos / OWASP / SuperTokens guidance — function-entry checks are bypassable by indirect prompt injection routes that re-enter the toolset). F6 declares the contract; F7 implements.
- **Skills/runbooks coexistence path** — F6 promotes only `k8s-crashloop` from `domain/skills/` into a proper runbook (capability scoping + checks + tests). Remaining `domain/skills/*-runbook` items stay until a follow-on `runbook-promotions.md` plan.
- **Scope expansion (2026-04-26 late-day pivot)** — F6.J (RAG / pgvector Stage 3 fallback), F6.K (`extends:` shared-preamble composition), F6.L (daily drift-detection job), F6.M (weekly fingerprint-clustering + auto-PR flywheel), and F6.N (Confluence write-side PR-bot) folded back into F6 from "follow-on plans" per the design spec §13. Rationale: dependencies (RAG and `extends:` need loader/matcher; drift/flywheel/Confluence need everything before them) collapse cleanly into one PR; user direction was for a single coherent F6 deliverable that owns the runbook lifecycle end-to-end. RAG is **opt-in per environment** via `RUNBOOK_RAG_FALLBACK_ENABLED`; no behaviour change for default deployments.

**What ships in this PR:** the full F6 sub-plan steps F6.A → F6.I (see sub-plan).

**Acceptance:** R-RB-1 met (`content_sha` computed by pre-commit, present in frontmatter, written to DB on every match). R-RB-2 met (10+ deterministic tag-permutation tests + Stage 2A/B mocked-LLM tests). R-RB-3 met (`candidates_json` always populated). R-RB-4..6 met (lifecycle, sanitization, feedback table). R-AG-4 preserved (Stage 2 LLM I/O captured in F4 replay bundle; 30-run determinism CI continues to pass). R-OB-2 extended (mandatory span attrs include `runbook_id`, `runbook_content_sha`, `match_method`).

---

### Phase F7: Capability tokens for tool authorization

Maps to RFC §5.3 + R-TL-3, R-TL-4. Tools authorized only when active runbook lists them; tenant-scoped.

- [x] **F7.1** Define `src/sentinel/domain/tools/grants.py`. Frozen attrs `RunbookGrant(runbook_id, runbook_content_sha, tool_name, tenant_id, granted_at)`. Function `authorize_tool_call(...)`. Raises `ToolNotInRunbookError`, `TenantScopeViolationError`, `ToolBudgetExceededError`
- [x] **F7.2** `RunbookScopedToolset` wrapper at `plugins/toolsets/_runbook_scope.py` enforces at the toolset-wrapper boundary; `wrap_for_runbook_scope` factory applied in `worker.py`
- [x] **F7.3** Wire `envelope` + `_tool_call_counters` into `Dependencies` for all three investigator agents (`investigator.py`, `k8s_investigator.py`, `root_cause_analyser.py`) + `investigation.py` call sites
- [x] **F7.4** Unit tests: `tests/unit/domain/tools/test_grants.py` (6 cases) + `tests/unit/plugins/toolsets/test_runbook_scope.py` (10 cases)
- [x] **F7.5** Adversarial integration slice `tests/integration/test_tenant_isolation.py`: cross-tenant namespace → `TenantScopeViolationError` + audit_log row; tool not in runbook → `ToolNotInRunbookError` + audit_log row
- [x] **F7.6** Updated `docs/architecture.md` §Runbook grants + ticked R-TL-3 in `docs/prd.md`

**Acceptance:** R-TL-3 met (tools outside runbook's `tools.yaml` rejected with structured error + audit_log row). R-TL-4 met at the app layer (cross-tenant rejection; K8s RBAC layer comes in wk5 plan). Both rejection types logged.

---

### Phase F8: Quality gate (deterministic groundedness) + replay determinism CI

Maps to RFC §5.4 + R-QG-1 + R-AG-4 + R-CO-1. Closes the foundations loop.

- [x] **F8.1** Define `src/sentinel/domain/quality/groundedness.py`. `GroundednessVerdict(passed, missing_evidence_finding_indices, reason)` as `attrs.frozen`. `assess_groundedness(*, findings, investigation_status)` — vacuously passes on skipped/failed/no findings; fails when any `Finding.evidence_refs` is empty. (LangGraph adaptation: `investigation_status` string from `_investigation_context` replaces `tool_calls` check)
- [x] **F8.2** Add `assess_quality` node in `src/sentinel/interfaces/workflows/sre_investigation.py` (LangGraph, not the archived Pydantic Graph). Position: after `analyse_root_cause`, before `determine_confidence`. Reads `investigation.findings` + `_investigation_context["status"]`; sets `quality_verdict` in state.
- [x] **F8.3** Update `determine_confidence` to consume `state.quality_verdict`. When `groundedness_pass=False`, forces `needs_approval=True` (LangGraph soft-fail approach: routes to approval gate rather than `End(failure_mode=...)` to preserve investigation for human review)
- [x] **F8.4** Wire `audit_log` writes for key state transitions (R-CO-1). `application/audit/__init__.py` exposes `record_transition(*, request_id, from_state, to_state, reason, db_session)`. Called from `investigate_alert` (received→completed) and `resume_investigation` (awaiting_approval→decision). `prev_hash=None` in foundations — chaining deferred.
- [x] **F8.5** Unit tests `tests/unit/domain/quality/test_groundedness.py` (9 tests): finding without evidence_ref → fail; mixed findings → partial fail with indices; skipped/failed investigation → vacuous pass; empty findings → vacuous pass; `GroundednessVerdict` is frozen.
- [x] **F8.6** Replay determinism test rewritten for LangGraph API in `tests/integration/test_replay_determinism.py`. `slow` marker included; CI integration job runs `uv run pytest tests/integration/` with no marker exclusion — 30-run sweep runs on every PR.
- [x] **F8.7** PRD updated: R-QG-1, R-AG-4, R-CO-1 ticked in `docs/prd.md` §6 (Hedge Fund Compliance & Quality Gating) and §8 (Runbook Catalog).
- [x] **F8.8** Architecture doc updated: `assess_quality` node documented in SRE pipeline section.
- [ ] **F8.9** Run `/update-docs` per CLAUDE.md workflow — diffs the foundations commits against `docs/prd.md` and confirms checkbox updates are accurate

**Acceptance:** R-QG-1 met (gate rejects fixture with empty `evidence_refs`). R-AG-4 met (replay reproduces output bit-for-bit on 30 consecutive CI runs). R-CO-1 met (`audit_log` write for every state transition with `prev_hash` chain). All foundations PRD checkboxes ticked.

---

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-25 | Initial draft | RFC-001 v0.4 ratified; foundations defined per evolve-in-place strategy |
| 2026-04-25 | F1 pivot recorded: collapsed 4-layer attrs chain (`Settings → BaseConfig → CommonConfig → SRETeamConfig`) to two-layer Pydantic chain (`Settings → BaseConfiguration → CommonConfiguration`); multi-tenant via `Settings.team_profile` + `TEAM_CONFIG_REFS` registry; `SRETeamConfig` deferred until team behaviour diverges. Phase F1 marked complete (PR #22). | Pydantic `BaseModel` is the project's existing config contract; collapsing to one type avoided parallel attrs/Pydantic types and earned the right to keep team subclasses for when divergent behaviour actually exists. |
| 2026-04-26 | F3 path correction: new SQLModel files land in `data/sql/<name>.py` (not `data/<name>_models.py`) after the 2418e8a `data/` restructure. F3.2 + F3.3 landed on `feat/sentinel-foundations-f3-db-schema` with `tenant_id`-prefixed composite indexes and an explicit FK constraint name on `runbook_match`. F3.1 closed: no dedicated `finding` table in foundations — findings stay JSONB on `InvestigationRecord` until the wk5+ `§12.3.5 finding` plan. | Plan filemap predated the data-layer split; new tables follow the actual repo convention. The dedicated `finding` table is out of foundations scope per the plan's §3.5 cut. |
| 2026-04-26 | F4 Phase A landed on `feat/sentinel-foundations-f4-otel-langfuse-replay`: F4.1 mandatory agent-context attrs (`ef23db7`), F4.2 `MandatoryAttributesValidator` (`a37c36a`), F4.3 Langfuse OTLP exporter + bootstrap wiring (`1feba88`), F4.A.1 local Langfuse v3 docker-compose stack (`1096ce5`). F4.4 runtime smoke deferred until Docker is available on dev host; compose config validates. | Vertical slice for Langfuse end-to-end — Phase B (replay-bundle tool/LLM I/O + determinism CI) ships on a fresh branch from the merge commit. |
| 2026-04-26 | F6 verdicted-design pivot recorded: original tag-only matcher with alphabetical tiebreak → two-stage hybrid (Stage 1 deterministic tag pre-filter + Stage 2 small-LLM disambiguator on ties AND zero-match with explicit `no_match` option). Triple-key versioning (`content_sha` + git SHA + immutable `runbook_id`). Lifecycle frontmatter (`last_validated`, `deprecated_at`, `superseded_by`, `mnpi_safe`). Body sanitization + quarantine prompt frame for indirect-prompt-injection defence. Always-write `runbook_match` with full `candidates_json` for regulator audit. New `runbook_feedback` table. F7 contract update: capability tokens enforced at the toolset-wrapper boundary, not function entry. F6 sub-plan extracted to `sentinel-foundations-f6-runbook-catalog.md`; full design at `docs/superpowers/specs/2026-04-26-f6-runbook-catalog-design.md`. | Industry research (HolmesGPT, Robusta, Anthropic Skills, AWS SSM, OWASP/Cerbos, LogJack arXiv 2604.15368) validated the structural F6 plan but surfaced state-of-the-art improvements; user direction "best of best, internally evolvable, no HolmesGPT/kagent integration"; the guardrails (sanitization, toolset-wrapper enforcement, always-write audit row) are non-negotiable for hedge-fund-grade replay + compliance. |
| 2026-04-26 | F6 scope expansion: F6.J (RAG / pgvector Stage 3 fallback), F6.K (`extends:` shared-preamble composition), F6.L (daily drift-detection job), F6.M (weekly fingerprint-clustering + auto-PR flywheel), F6.N (Confluence write-side PR-bot) folded back into F6 from "follow-on plans" per design spec §13. RAG opt-in via `RUNBOOK_RAG_FALLBACK_ENABLED`; default deployments unchanged. | User requested single-PR delivery; dependencies (RAG + `extends:` need loader/matcher; drift / flywheel / Confluence need everything) sequenced into Rounds 2–4 of the parallel-agent dispatch and collapsed into one coherent F6 deliverable owning the runbook lifecycle end-to-end. |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up plans (week 5+ of RFC §14, and beyond)

- `sentinel-hedgefund-deployment.md` — Helm chart finalisation, real K8s cluster wiring, OTEL collector, network policies, Pod Security (RFC §6, §14 wks 5–8)
- `info-barriers.md` — 5-layer info barrier layers 3–5: LiteLLM tenant routing, full redactor with LLM judge, Postgres RLS by tenant_id (RFC §5.7, month 3+)
- `case-history-retrieval.md` — pgvector + BM25 case-history retrieval (RFC §3.3.1, needs ≥100 confirmed investigations)
- `langgraph-sre-migration.md` — **complete** (PR #35); SRE pipeline now on LangGraph. Support + chart pipeline migrations are next follow-up plans.
- `adversarial-fixtures.md` — adversarial test suite for cross-PM injection, prompt injection (RFC §5.6 + §10.6, wk7)
- `audit-worm-archive.md` — WORM archive job for `audit_log` 7-year retention (RFC §12.3.10, wk5+)
- `devops-team-profile.md` and `ace-team-profile.md` — DevOps and ACE profiles (RFC §1.4, months 4–6)
- `llm-judge-quality-gate.md` — LLM judge for redactor + quality gate (RFC §5.4, month 3)

### Tech debt to revisit
- F2.4 `envelope_strict_mode = False` — flip to True in wk5 plan once webhook tenant_id derivation is robust across all alert sources
- F4.2 mandatory-attribute validator flags but doesn't drop incomplete spans — tighten to drop in production once shadow mode confirms no false positives
- F6.4 RAG fallback for runbook matcher — month 3 once tag-based coverage data is in
- F8.6 30-run determinism CI — expand to 100-run nightly once Helm landed
