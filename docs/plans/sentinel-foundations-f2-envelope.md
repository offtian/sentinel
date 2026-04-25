# Plan: Sentinel Foundations — Phase F2 Identity & Envelope Propagation

**Status:** complete
**Created:** 2026-04-25
**Last updated:** 2026-04-25

## Goal

Land the F2 phase of the foundations plan: mint `request_id` at FastAPI ingress and carry an immutable `Envelope` (`request_id`, `tenant_id`, `cluster_id`, `region`, `pii_class`, `received_at`) through every webhook entry, pipeline node, OTel span, and structlog log line. RFC §3.1, R-IN-3, R-IN-4.

Parent plan: [`sentinel-hedgefund-foundations.md`](sentinel-hedgefund-foundations.md) (specifically F2.1 through F2.9).

## Scope

### In scope

- `Envelope` `attrs.frozen` primitive in `data/envelope.py` carrying the six RFC §3.1 fields plus the `to_log_context()` / `to_span_attributes()` view helpers and the `is_redacted_pii_class()` public predicate.
- `RequestIdMiddleware` (`BaseHTTPMiddleware` subclass) at `interfaces/api/middleware.py`, wired into `app.py`.
- `envelope_factory` module composing the envelope from PagerDuty / Datadog / Jira / manual payloads, plus `EnvelopeIngressError` and the `envelope_strict_mode` flag on `BaseConfiguration`.
- SRE and support pipelines (`State.envelope`, every node binds log + span context via `run_node_with_envelope`, entry points require `envelope=` kwarg).
- Worker rehydrates the envelope from the queued payload (preserves ingress correlation id and PII class).
- Per-invocation placeholder envelopes for replay, chat, and Slack (each documented with the phase that retires it).
- Unit tests (envelope, middleware, factory, pipeline propagation) and an integration test exercising the full end-to-end chain in-process.

### Out of scope

- DB-row `ingress_request_id` column (deferred to F3 schema gap-fill).
- Langfuse OTLP span export of the six envelope attributes (deferred to F4 — today only the in-process exporter sees them).
- Real tenant resolution for chat / Slack surfaces (chat hosts only the local operator; Slack scoping by channel is good enough until those surfaces carry tenant context).
- Capability-token enforcement on the envelope's `tenant_id` (F7).
- Per-PM Langfuse RBAC (deferred to month 3+).

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| File placement of `Envelope` | `src/sentinel/data/envelope.py`, **not** `src/sentinel/domain/envelope.py` as the parent filemap suggested. | The python-conventions rule explicitly lists "identity envelopes" as a `data/` example. `data/` sits below `domain/` in the import-linter layer order, so every consumer (`config`, webhook handlers, middleware, pipelines) imports freely while `data/` itself stays free of upper-layer dependencies. F1 hit the same lesson with `policies.py`. |
| `PIIClass` typing | `Literal["public", "internal", "confidential", "mnpi"]` re-exported alongside `Envelope`. | One canonical alias, matching `ApprovalPolicy`'s `ApproverRole` pattern. |
| Redaction at the log boundary, not the span boundary | `to_log_context()` swaps `tenant_id` for `tenant_hash` when `pii_class in ("confidential", "mnpi")`; `to_span_attributes()` keeps the raw `tenant_id`. | Spans are not the redaction boundary — downstream exporters apply policy. Logs are emitted continuously and are the first place a raw tenant id leaks; redact there. `request_id` (UUID) is never PII so it stays in every context. |
| Datetime guard | `attrs.validators` on `received_at` rejects naive datetimes AND non-UTC tz-aware datetimes. | The whole project is tz-aware UTC; failing loud at construction prevents silent UTC misinterpretation downstream. |
| Strict-mode flag default | `envelope_strict_mode = False` for foundations (warn + fall back to `tenant_id="unknown"`). | RFC §10.1 R-IN-3 says hard-fail in production; foundations chooses dev velocity. The flag is a one-line flip when production is ready. |
| `EnvelopeIngressError` shape | `source` + `request_id` + `missing_tenant_id` flag. | Routers surface the error as a 422 with a stable JSON shape callers can branch on without parsing the message. The boolean flag makes it machine-readable for future ingress failure modes. |
| Tenant slug sanitisation | Lowercase + collapse whitespace + drop non-`[A-Za-z0-9_-]` + truncate to 63 chars (k8s namespace limit). | A hostile webhook payload (long service summary, weird unicode) cannot bloat downstream rows or break k8s-namespace assumptions. |
| Pipeline `State.envelope` field | Required, no default; positional `State(alert)` no longer constructs. | Forces every caller to think about envelope explicitly. The kwargs-only constructor (`State(alert=alert, envelope=envelope)`) eliminates silent field-swap bugs. |
| Single helper `run_node_with_envelope` | Wraps the existing `instrumented_node_run` to add structlog binding + OTel attribute setting. | Three concerns (metrics duration, log context, span attrs) compose into one call site per node. The optional `envelope` kwarg on `instrumented_node_run` is currently dead code in production (only its own unit tests exercise it) — flagged as tech debt. |
| Worker envelope rehydration | Read `ingress_request_id` / `pii_class` / `tenant_id` / `cluster_id` / `region` from the queued payload; mint a fresh UUID only when missing/malformed. | The whole point of F2 is correlation; defaulting to `uuid.uuid4()` on every job claim breaks the chain across the queue. |
| Placeholder envelopes (replay, chat, Slack) | Per-invocation, `pii_class="internal"` (never `"public"` — never skip future redaction). | Each call wants its own request_id; minting once at startup would collapse all sessions into one correlation id. The conservative `pii_class` makes future elevation a one-line change. |

## Steps

- [x] **F2.1** `Envelope` `attrs.frozen` in `data/envelope.py` per RFC §3.1.
- [x] **F2.2** `RequestIdMiddleware` in `interfaces/api/middleware.py`.
- [x] **F2.3** Wire middleware in `interfaces/api/app.py`.
- [x] **F2.4** `envelope_factory` + `EnvelopeIngressError` + `envelope_strict_mode` flag + router wiring (SRE + support).
- [x] **F2.5** `State.envelope` required field on both pipelines; entry points require `envelope=` kwarg.
- [x] **F2.6** `run_node_with_envelope` sets the six envelope-owned mandatory OTel span attributes per RFC §13.2.
- [x] **F2.7** Same helper binds `envelope.to_log_context()` onto structlog contextvars at every node (auto-cleans on exception).
- [x] **F2.8** PII redaction in `to_log_context()` (sha256-truncated-to-12 `tenant_hash` for `confidential` / `mnpi`).
- [x] **F2.9** Unit tests (22 envelope, 11 middleware, 31 factory, 25 pipeline propagation, 13 router) and 9 integration tests covering the full webhook → response → span → log chain.
- [x] **F2.aux** Worker rehydrates envelope from queued payload; replay / chat / Slack callers mint per-invocation placeholders with documented retirement timelines.
- [x] **F2.docs** Foundations plan F2 section ticked off with "what landed" body. INDEX.md updated. Architecture.md gains an Identity & Envelope section. `.env.default` documents `REGION` + `ENVELOPE_STRICT_MODE`.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-25 | Initial draft scoped only to F2.1+F2.8 (the envelope primitive slice). | Three parallel implementer agents picked up F2.2..F2.7 and F2.9 against the public surface frozen here. |
| 2026-04-25 | Plan expanded to cover the full F2 phase as the slices landed. Filemap delta: `Envelope` lives at `data/envelope.py` (parent filemap pointed at `domain/envelope.py`) — same layering rationale as F1's `data/policies.py`. | Sub-plan now mirrors the parent's F2 acceptance criterion and serves as the canonical step-level reference. |

## Outcome

### What was delivered

- **Envelope primitive** at `src/sentinel/data/envelope.py` with the six RFC §3.1 fields, `to_span_attributes()`, `to_log_context()` (with PII redaction by class), `is_redacted_pii_class()` predicate, and a tz-aware-UTC validator on `received_at`.
- **Ingress middleware** at `src/sentinel/interfaces/api/middleware.py` — `RequestIdMiddleware` mints / echoes / re-mints UUIDs, binds `structlog.contextvars`, attaches the id to the current OTel span, and propagates the response header. Exception-path cleanup via `try/finally`.
- **Envelope factory** at `src/sentinel/interfaces/webhooks/envelope_factory.py` — `envelope_from_pagerduty/datadog/jira/manual` builders, `EnvelopeIngressError` (with `missing_tenant_id` flag), `_sanitize_tenant_slug` capped at 63 chars.
- **Strict-mode flag** at `BaseConfiguration.envelope_strict_mode` (env: `ENVELOPE_STRICT_MODE`, default `False`) — flips soft-fail to a 422 hard-fail with a stable JSON error shape.
- **Pipeline propagation** in both SRE and support graphs — `State.envelope` required, every node uses `run_node_with_envelope`, entry points (`investigate_alert`, `review_ticket`) require `envelope=` kwarg.
- **Caller updates** in `worker.py` (rehydrates from queued payload), `replay.py`, `interfaces/chat/app.py`, `interfaces/slack/event_handlers.py` (per-invocation placeholders).
- **Test suite** — 22 envelope unit tests, 11 middleware tests, 31 envelope-factory tests, 25 pipeline propagation tests, 13 router tests, and 9 end-to-end integration tests proving the chain. Total branch additions: 877 unit + 51 integration + 76 functional/eval, all green.

### Follow-up / tech debt

- **Hoist duplicate router helpers** — `_envelope_ingress_failure_response` and `_envelope_payload` are byte-identical copies in `routers/sre/router.py` and `routers/support/router.py`. Hoist into `envelope_factory` as public helpers; or even better, add `Envelope.to_job_payload()` so the dict shape lives next to the primitive.
- **`envelope_factory.envelope_placeholder()`** — chat / Slack / replay each hand-roll the same placeholder Envelope. A single factory accepting `source` + optional overrides shrinks each call site to a one-liner.
- **Promote `_UNKNOWN_TENANT`** to `data/envelope.py` as a public constant. Today the `"unknown"` literal is re-declared in worker, replay, chat, Slack, and the factory.
- **Drop `instrumented_node_run`'s optional `envelope` kwarg** — production goes through `run_node_with_envelope` exclusively. Inline the OTel-attr setting into the wrapper and rewrite the two helper-tests to drive `run_node_with_envelope` directly. One wrapper, one concern.
- **Lift the 63-char tenant cap** out of `_sanitize_tenant_slug` and into the `_finalise_tenant_id` step in the factory, so k8s namespace, Datadog tag, and Jira project key sources honour the cap too.
- **Cache `to_log_context()` / `to_span_attributes()`** outputs at construction time — once F4 elevates `pii_class` onto the hot path, the per-emit sha256 fires 9× per pipeline run. Today the hash is dormant in F2's default `pii_class="internal"`, so the optimisation is post-F4.
- **Chart pipeline** (`interfaces/graphs/chart_generation.py`) was not updated with envelope propagation. Off the F2 critical path; revisit when chart_generation gets multi-tenant traffic.
- **Hoist the `recorded_spans` test fixture** into `tests/conftest.py` — duplicated between `test_middleware.py` and `test_request_id_propagation.py`. Encapsulates the OTel `_TRACER_PROVIDER` foot-gun in one place.
- **Replay envelope persistence** — `replay.py`'s `_envelope_for_replay` mints a fresh `request_id` because `ReplayBundle` doesn't carry envelope fields yet. F4.5 retires the placeholder when bundle persistence ships.
- **DB-row `ingress_request_id`** — F3 schema gap-fill adds the column; the integration test docstring already flags this assertion as "until F3 lands".
- **Langfuse span export** — F4 wires the OTLP exporter; the integration test asserts only on the in-process exporter today.
- **Drop F-phase ticket references** from source comments per `.claude/rules/python.md` ("ticket numbers belong in the PR description, not the code"). RFC section references stay.
