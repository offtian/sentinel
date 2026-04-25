# Plan: Sentinel Hedge Fund — Foundations (RFC-001 v0.4)

**Status:** draft
**Created:** 2026-04-25
**Last updated:** 2026-04-25
**Progress:** 0/N steps complete

## Goal

Evolve the existing Sentinel codebase into the foundations of the hedge-fund-grade SRE platform described in `Sentinel/RFC-001-sentinel-hedgefund.md` v0.4. "Foundations" means RFC §14 weeks 0.5–4: validation sprint, config layering, identity propagation, the OTEL → Langfuse → replay-bundle triple (the single failure mode to fear, §14.7), LiteLLM proxy migration, runbook catalog, capability tokens, and a deterministic groundedness gate. Weeks 5–8 (real cluster, Helm, HolmesGPT integration breadth, adversarial suite, soft launch) become a follow-up plan.

The strategy is **evolve in place**, not greenfield. RFC §15.14 v0.4 selects PydanticAI + LangGraph; the current codebase is already PydanticAI + Pydantic Graph. The orchestration-framework migration (Pydantic Graph → LangGraph) is intentionally deferred behind an ADR and is **not** in foundations scope — the replay determinism payoff is real but the framework swap mid-foundations would block every other phase.

## Scope

### In scope

- Validation sprint for tentative decisions D-11..D-16 + O-10 (each gets a one-page ADR with named-owner sign-off)
- Config layering refactor: `Settings` → `BaseConfig` → `CommonConfig` → `SRETeamConfig`
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
| §3.7 publish | `domain/sre/publish.py` + Slack/PagerDuty adapters |
| §3.8 trace bundle (partial) | PR #15 prompt-versioning-and-replay |
| §4.5 skills runtime | `domain/skills/` (runbooks layer in F6 sits above skills) |
| §5.8 HolmesGPT | `domain/sre/holmes_adapter.py` |
| §10.4 K8s investigation backends | `domain/sre/k8s_native_agent.py`, `kagent_adapter.py` (PR #20) |
| §10.5 OTEL spans (partial) | `data/tracing_models.py`, `bootstrap_otel.py` |
| Token usage + cost | PR #18 |

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Repo strategy | Evolve existing Sentinel codebase in place | RFC §15.14 v0.4 picks PydanticAI + LangGraph — current stack is already PydanticAI; preserves runbook/MCP/K8s/audit work; framework deltas isolate to LiteLLM transport, config layering, and OTEL/Langfuse wiring |
| Agent framework | PydanticAI (no change) | RFC D-01 v0.4 confirms; `instrument=True` already wired across agents |
| Orchestration framework | Stay on Pydantic Graph for foundations; revisit LangGraph at month 3 (ADR 0007) | LangGraph's checkpoint replay is attractive but framework swap mid-foundations would block F4–F8; PR #15 already covers replay determinism via the bundle approach |
| Strangler vs big-bang | Strangler everywhere | Existing pipelines must keep working at every phase boundary; LiteLLM proxy + runbook catalog + capability tokens coexist with current code until F8 |
| Config layering | Refactor existing `BaseConfiguration` (`config.py`) into 4-layer chain (`Settings` → `BaseConfig` → `CommonConfig` → `SRETeamConfig`) per RFC §15.4 | One file per layer, inheritance not composition, single `team_id` abstract method |
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
Phase F1 — Config layering: Settings → BaseConfig → CommonConfig → SRETeamConfig    wk 1
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
| §1.4 multi-team profiles | Single `BaseConfiguration` class — needs 4-layer chain so DevOps/ACE can plug in later |
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

src/sentinel/plugins/teams/__init__.py                      # F1: TypeAlias union of TeamConfig
src/sentinel/plugins/teams/sre/__init__.py                  # F1: SRETeamConfig
src/sentinel/plugins/common/__init__.py                     # F1: substrate handles
src/sentinel/plugins/common/common.py                       # F1: CommonConfig
src/sentinel/plugins/common/approval.py                     # F1: ApprovalPolicy primitive
src/sentinel/plugins/common/output.py                       # F1: OutputChannel primitive
src/sentinel/plugins/common/redaction.py                    # F1: RedactionPolicy primitive

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
src/sentinel/settings.py                                    # F1: env-only fields, drop logic
src/sentinel/config.py                                      # F1: BaseConfig (shape) + get_config() entry point
src/sentinel/plugins/config.py                              # F1: legacy CommonConfiguration → wrapper, then deprecate

src/sentinel/data/audit_models.py                           # F3: WORM constraints + request_id
src/sentinel/data/models.py                                 # F3: extend InvestigationRecord toward `investigation` shape
src/sentinel/data/tracing_models.py                         # F3: extend AgentCallRecord toward `tool_call` shape
src/sentinel/bootstrap_otel.py                              # F4: Langfuse exporter wiring
src/sentinel/utils/replay.py                                # F4: thin shim, delegates to replay_bundle.py

src/sentinel/interfaces/graphs/sre_investigation.py         # F2/F6/F7/F8: envelope propagation, MatchRunbook node, capability gate, AssessQuality node
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

Maps to RFC §15. Refactor existing `BaseConfiguration` into the 4-layer chain. Strangler — every existing call to `get_config()` keeps returning a working object.

- [ ] **F1.1** Define `BaseConfig` skeleton in `src/sentinel/config.py` per RFC §15.4. `attrs.frozen(slots=True, kw_only=True)` with placeholder defaults: `investigation_loop_cap: int = 0`, `investigation_timeout_seconds: int = 0`, `confidence_publish_min: float = 0.0`, `redaction_policy: RedactionPolicy = empty`, `allowed_tools: frozenset[str] = frozenset()`, `output_channels: tuple[OutputChannel, ...] = ()`, `system_prompts: Mapping[str, str] = MappingProxyType({})`, `approval_policy: ApprovalPolicy = empty`, `model_id_primary: str = ""`, `model_id_judge: str = ""`, `runbooks_paths: tuple[Path, ...] = ()`. Plus `team_id` abstract method (single discriminator)
- [ ] **F1.2** Move env-var ingestion into `src/sentinel/settings.py` only — drop logic. Add fields per RFC §15.3: `team_profile: Literal["sre", "devops", "ace"]`, `litellm_base_url: HttpUrl | None`, `litellm_virtual_key: SecretStr | None`, `langfuse_host: HttpUrl | None`, `langfuse_public_key: SecretStr | None`, `langfuse_secret_key: SecretStr | None`, `otel_collector_endpoint: HttpUrl | None`, `runbooks_root: Path`. Keep existing fields (`alert_classifier_llm`, etc.) for backwards compatibility
- [ ] **F1.3** Create `src/sentinel/plugins/common/common.py` with `CommonConfig(BaseConfig)` filling shared defaults per RFC §15.5: `investigation_loop_cap = 8`, `investigation_timeout_seconds = 300`, `confidence_publish_min = 0.7`, `confidence_human_review_min = 0.4`, `case_retrieval_top_k = 5`, `enable_replay_bundle = True`. Move infra-client factories (`build_litellm_client`, `build_langfuse_client`, `build_db_session_factory`) from existing `plugins/config.CommonConfiguration` into this class as `@property`/`functools.cached_property`
- [ ] **F1.4** Create primitives: `src/sentinel/plugins/common/approval.py` (`ApprovalPolicy`), `output.py` (`OutputChannel`), `redaction.py` (`RedactionPolicy`). All `attrs.frozen` per RFC §15.9
- [ ] **F1.5** Create `src/sentinel/plugins/teams/__init__.py` defining `TeamConfig: TypeAlias = "SRETeamConfig"` (string forward-ref to dodge circular import). Add `# devops/ace land in later plans` comment
- [ ] **F1.6** Create `src/sentinel/plugins/teams/sre/__init__.py` with `SRETeamConfig(CommonConfig)` per RFC §15.7. Concrete values: `team_id = "sre"`, allowed tools (k8s_, prom_, harness_ prefixes — start with current toolset names), output channels (Slack `#sre-oncall`, PagerDuty), `model_id_primary` from `settings.root_cause_llm`, `runbooks_paths = (settings.runbooks_root / "sre", settings.runbooks_root / "common")`
- [ ] **F1.7** Update `get_config()` in `src/sentinel/config.py` per RFC §15.11. `lru_cache`-wrapped, dispatches on `settings.team_profile`. For now, only `"sre"` returns `SRETeamConfig`; `"devops"`/`"ace"` raise `NotImplementedError` with explicit "see plan X" pointer. Backward-compat: existing callers `get_config()` returns the SRE config object that satisfies `BaseConfig`
- [ ] **F1.8** Update import-linter contracts in `pyproject.toml`: `plugins/teams/sre` may not import `plugins/teams/devops` or `plugins/teams/ace` (when those land); `plugins/common` may not import any `plugins/teams/*`; `domain/*` may not import any `plugins/*`
- [ ] **F1.9** Migrate all existing callers of `BaseConfiguration` / `CommonConfiguration` to the new 4-layer types. Keep `plugins/config.CommonConfiguration` as a thin deprecated alias (`CommonConfiguration: TypeAlias = SRETeamConfig` with a `@deprecated` decorator) for one release. Fix imports per `python.md` rule (module-level only, import modules not objects)
- [ ] **F1.10** Unit tests `tests/unit/test_config_layering.py`: assert `BaseConfig` cannot be instantiated directly (abstract `team_id`); `CommonConfig` fills loop_cap=8, redaction policy non-empty; `SRETeamConfig.team_id == "sre"`; allowed_tools is a frozenset; testing pattern via factory injection per RFC §15.13 (no `os.environ` mutation in tests)
- [ ] **F1.11** Update `.env.default` with new env vars + comments documenting each. Update `docs/architecture.md` §Configuration with the new 4-layer diagram (mermaid classDiagram from RFC §15.1)
- [ ] **F1.12** Run `just lint && just test` — all green. Confirm existing pipelines run unchanged via `just run-api` smoke

**Acceptance:** `get_config()` returns an `SRETeamConfig` instance that satisfies `BaseConfig`. Existing call sites unchanged. R-OB-2 unblocked (configs carry `team_id` for span tagging). Import-linter contracts pass. Backward-compat alias survives one release.

---

### Phase F2: Identity & envelope propagation

Maps to RFC §3.1, R-IN-3, R-IN-4. Mint `request_id` at FastAPI ingress; carry `tenant_id` / `region` / `pii_class` through every span and DB row.

- [ ] **F2.1** Define `src/sentinel/domain/envelope.py` with `Envelope` `attrs.frozen` per RFC §3.1: `request_id: UUID`, `tenant_id: str`, `cluster_id: str`, `region: str`, `pii_class: Literal["public", "internal", "confidential", "mnpi"]`, `received_at: datetime` (UTC, tz-aware). Plus `to_log_context()` returning `dict[str, str]` for structlog binding and `to_span_attributes()` returning `dict[str, AttributeValue]` for OTel
- [ ] **F2.2** Create `src/sentinel/interfaces/api/middleware.py` with `RequestIdMiddleware`: read `X-Request-Id` header → mint UUID4 if absent → set on `request.state.request_id` → bind to `structlog.contextvars` → set OTel current span attribute `request_id` → propagate as response header. ASGI middleware (matches FastAPI 0.110+ pattern)
- [ ] **F2.3** Wire middleware in `src/sentinel/interfaces/api/__init__.py` (or wherever the FastAPI app is constructed — find via `grep -r "FastAPI("` if uncertain). Integration test: `curl -H "X-Request-Id: abc" /health` returns same id in response header
- [ ] **F2.4** Update webhook handlers in `src/sentinel/interfaces/webhooks/` (PagerDuty, Datadog, AlertManager when added) to construct `Envelope` from request payload. `tenant_id` derivation: from k8s namespace label if present, else from PagerDuty service tag, else fallback to `"unknown"` and emit warning log (RFC §10.1 R-IN-3 makes this hard-fail in production; for foundations we warn-and-continue with tenant_id="unknown" to keep dev velocity, hard-fail flag added in `SRETeamConfig.envelope_strict_mode = False` for now)
- [ ] **F2.5** Update pipeline `State` in `src/sentinel/interfaces/graphs/sre_investigation.py` to carry `envelope: Envelope`. Every node has access. Update existing nodes' first-line — no business-logic changes, just propagation
- [ ] **F2.6** Update `instrumented_node_run()` in `src/sentinel/interfaces/graphs/_node_helpers.py` to set OTel span attributes from `state.envelope.to_span_attributes()`. Verify in F4.1 that all 6 mandatory attributes per RFC §13.2 land on every span
- [ ] **F2.7** Update logger contexts to bind envelope. Replace ad-hoc `logger.bind(alert_id=...)` with `logger.bind(**envelope.to_log_context())` in every node. Search-and-replace candidates: `grep -rn "logger.bind" src/sentinel/interfaces/graphs/`
- [ ] **F2.8** PII redaction at the envelope/log boundary. In `domain/envelope.py.to_log_context()`, when `pii_class in ("confidential", "mnpi")`, redact raw `tenant_id` to `tenant_hash = sha256(tenant_id)[:12]`. Tests asserting redaction behaviour
- [ ] **F2.9** Unit tests `tests/unit/test_envelope.py`: construction, `to_span_attributes` shape, `to_log_context` redaction by pii_class. Integration test `tests/integration/test_request_id_propagation.py`: webhook → request_id in response → request_id in DB row (after F3) → request_id in spans (after F4)

**Acceptance:** Webhook POST generates UUID `request_id` echoed in response header, OTel spans, and DB rows. `pii_class` controls log redaction. R-IN-3 met (envelope minted before downstream stage; in foundations, soft-fail warns instead of hard-fails — see F2.4 note for production-hardening followup).

---

### Phase F3: DB schema gap-fill (8 canonical tables)

Maps to RFC §12.3. Add the 4 missing canonical tables; tighten 4 existing tables. All migrations reversible.

- [ ] **F3.1** Audit existing schema vs RFC §12.3. Confirm: `models.InvestigationRecord` ≈ RFC `investigation` (rename + extend), `audit_models.AuditLogRecord` ≈ RFC `audit_log` (add WORM constraints), `tracing_models.AgentCallRecord` ≈ RFC `tool_call` (rename + extend), `models.FindingRecord` (verify exists; check schema). Document the precise column delta for each as comments at the top of each new migration file
- [ ] **F3.2** Create `src/sentinel/data/alert_request_models.py` with `AlertRequestRecord` SQLModel per RFC §12.3.1: PK `request_id: UUID`, `tenant_id: str (indexed)`, `received_at: datetime UTC`, `provider: Literal["pagerduty", "datadog", "alertmanager"]`, `alert_id: str`, `severity: str`, `redacted_annotations: JSONB`, `dedup_status: Literal["new", "duplicate"]`. Migration `alembic/versions/008_alert_request_table.py`. Indexes per RFC §12.4: `(tenant_id, received_at desc)` and `(provider, alert_id)` for dedup lookups
- [ ] **F3.3** Create `src/sentinel/data/runbook_models.py` with `RunbookMatchRecord` per RFC §12.3.2: PK `match_id: UUID`, FK `request_id`, `runbook_id: str`, `runbook_version_sha: str (32 chars)`, `match_method: Literal["tag", "rag", "generic_fallback"]`, `match_confidence: float`, `matched_at: datetime UTC`. Migration `009_runbook_match_table.py`
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
- [ ] **F6.6** Add new pipeline node `MatchRunbook` in `src/sentinel/interfaces/graphs/sre_investigation.py`. Position: after `ClassifyAlert`, before `InvestigateWithHolmes` (or `K8sInvestigator` depending on backend). Reads `state.envelope` + `state.alert`; calls `matcher.match_runbook`; writes `runbook_match` row from F3.3 via existing `domain/audit/` writer pattern; sets `state.runbook = matched_runbook`
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
- [ ] **F8.2** Add new pipeline node `AssessQuality` in `src/sentinel/interfaces/graphs/sre_investigation.py`. Position: after `AnalyseRootCause`, before `DetermineConfidence`. Reads `state.findings` + `state.tool_calls`; runs `assess_groundedness`; writes `quality_verdict` row from F3.5; sets `state.quality_verdict`
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
