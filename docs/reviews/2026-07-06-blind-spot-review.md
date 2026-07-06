# Sentinel Blind-Spot Review — Unknown Unknowns

> **FROZEN DOCUMENT** — Point-in-time snapshot from 2026-07-06 at commit `9a4a1a5`.
> Preserved for historical context; do not update in place. Current status is tracked in
> `docs/prd.md` (acceptance criteria) and `docs/plans/INDEX.md`.
>
> **Purpose:** This is a *blind-spot* pass, not a design review. The goal is to surface
> problems the author has **not** realised — unknown unknowns — rather than to optimise
> known design choices. Findings are classified **High / Medium / Deferrable** by the cost
> of leaving them unaddressed, and each is tagged with whether it **survives the "Claude
> Agent SDK reframe"** described in §0 (i.e., would it still matter if the orchestration
> layer were rebuilt on a free agent loop?).

**Method.** Read PRD, architecture doc, plan index. Fanned out three read-only sweeps
(security/trust-boundary, reliability/operational, doc-vs-code drift). Every load-bearing
claim was independently verified against source before inclusion. All three sweeps ultimately reported
(the reliability sweep late, after its findings channel was fixed and after it provisioned a working
toolchain that produced the definitive QA-gate result in §2.3); every concrete code claim from all three
was re-verified against source before inclusion, and the reliability sweep's early-silence gap was also
covered first-hand. Claims that could **not** be confirmed are explicitly marked UNCONFIRMED.

---

## §0 — Headline: the framework bet was backwards (the central map-vs-territory finding)

This is question 5 ("where did I mistake map for territory?") answered by production reality,
and it reframes everything below.

**The map:** *A trustworthy production agent needs a deterministic orchestration graph plus a
pre-scripted, content-hashed runbook contract layer.* Nearly the whole codebase is built to
serve this belief — LangGraph nodes, the F6 three-stage runbook matcher, F7 capability
tokens, F4 byte-for-byte replay determinism, dual-framework coexistence.

**The territory (from production):** The fixed graph
(`classify → match_runbook → investigate → analyse → determine_confidence`) is *the thing that
caps the agent's intelligence* — it can't decide "metrics look clean, let me re-query logs with
a different filter" because the edges are hard-coded. In production the investigation agent was
rebuilt on the **Claude Agent SDK** with **minimal guardrails** (bash + PromQL + Loki, with
**read-only guardrails on bash**) and produced far more intelligent tool use than the entire
apparatus in this repo.

**The nuance that prevents over-correction:** compliance is a *separate axis* from
orchestration, and this repo conflated them.

- **Coupled to the graph → correctly discarded:** fixed node sequencing, runbook pre-scripting
  of the investigation, per-runbook capability tokens, and especially **F4's byte-identical
  replay determinism** — that goal is *actively incompatible* with a free agent loop (which is
  non-deterministic by design). The 30-run determinism CI defends a property you no longer want.
- **Orthogonal to the graph → worth keeping, wraps a free loop fine:** append-only audit log,
  human approval gate, per-call trace/token capture, and read-only tool enforcement. None need
  a graph.

**Corollary already visible in the code:** the production instinct — *enforce read-only at the
tool boundary, always on, fail-closed* — is strictly better than the repo's `RunbookScopedToolset`,
which **fails open** on the no-runbook path and isn't applied to MCP toolsets at all (§1.3).
Simple-and-always-on beat elaborate-and-conditional.

**The one decision that governs this whole document (question 4):** *Is the PydanticAI/LangGraph
codebase now legacy, superseded by the Claude Agent SDK build — or are they parallel?* If it is
being retired, the dual-pipeline drift (§2.5) and much of F6/F7 (§2.4) are moot and the review
collapses to "carry forward the auth gap, the guardrail shape, and the audit/approval layer." If
it still ships, everything below stands.

---

## §1 — HIGH-RISK blind spots

### 1.1 The entire HTTP surface is unauthenticated — including the approval gate

*Survives reframe: YES (any deployment needs this).*

There is no auth anywhere on the FastAPI app: no middleware, no security scheme, no route
dependency. Verified: grepping `Depends|Security|HTTPBearer|APIKeyHeader|Authorization|hmac|
compare_digest` across `interfaces/api/` and `interfaces/webhooks/` returns nothing but the DB
dependency; the only registered middleware is `RequestIdMiddleware` (`interfaces/api/app.py:76`).

- **Webhooks** (`/webhooks/pagerduty`, `/datadog`, `/jira`) verify **no** signature/secret.
  PagerDuty parsing reads `payload.get(...)` with no v3 HMAC check (`interfaces/webhooks/pagerduty.py`);
  same for Datadog and Jira. There are no settings fields for any webhook secret. Anyone who can
  reach the pod can forge alerts/tickets.
- **Approval gate** (the platform's primary safety control) is unauthenticated and the approver is
  a **free-text string in the request body** (`ApprovalAction.reviewer`, `sre/router.py:281`). The
  "audit trail" logs whatever name the caller typed (`sre/router.py:335,364`). So the compliance
  control that gates auto-publishing can be satisfied by any anonymous caller impersonating any
  reviewer — there is no accountable record of who approved.
- **MCP server**: an API-key middleware exists (`interfaces/mcp/server.py:198`, real `hmac.compare_digest`)
  but is **never wired in production** — it lives only inside `build_asgi_app` (line 241), while the
  actual entrypoint calls `mcp.run(transport="streamable-http")` (line 267), serving the raw app with
  no middleware. `docker-compose.yml` and Helm both expose port 8811. An operator who sets
  `MCP_SERVER_API_KEY` expecting protection gets none (verified).

**Why it's a blind spot:** the PRD frames Sentinel as hedge-fund-compliance software with human
approval gates and regulatory audit trails, yet the approval identity is self-asserted and the
whole surface is open. The compliance story has no floor without authentication + accountable
approver identity.

### 1.2 The "quality over time" story is largely cosmetic

*Survives reframe: YES — this is about measurement, not orchestration.*

Two of the three feedback loops the PRD/architecture lean on don't measure what they claim.

- **Confidence scoring is theater where it counts.** `ConfidenceScore.from_factors` weights source
  count 30% / relevance 50% / recency 20%. But at the call sites: `recency` is a **hardcoded constant**
  (`0.8` in `sre_investigation.py:777`, `0.7` in `support_review.py:317`), and `relevance` — the
  dominant 50% factor — is just the **LLM's own self-reported confidence** (`relevance=analysis.confidence`,
  `sre_investigation.py:643,776`; `relevance=self.raw_confidence`, `support_review.py:316`). So the
  "multi-factor" score reduces to *30% finding-count heuristic + 50% the-LLM-grading-itself + 20% a
  literal constant.* The approval gate at 0.7 hinges substantially on the model's self-assessment,
  which is exactly what a calibrated gate is supposed to avoid.
- **Evals don't test the model, don't run in CI, and are currently import-broken.** `tests/evals/
  test_investigation_evals.py:4` states cases run "through the pipeline with **mocked LLM agents**";
  `test_eval_framework.py` mocks the semantic judge so "no API calls are made"; and the eval runner
  grades the pre-recorded `case_payload["output"]` via a no-op task (`evals/runner.py`), so it never
  invokes a real agent even when working. Worse, `just test-evals` **doesn't even collect**:
  `tests/evals/conftest.py:6` imports `mock_holmes` (archived/commented out at `functional/conftest.py:48-56`)
  and `test_investigation_evals.py:21` imports `sentinel.interfaces.graphs.investigation` (moved to
  `_archive/`). The suite has been import-broken since those moves and nothing noticed — precisely
  because CI runs lint + `tests/unit/` + `tests/integration/` + docker build and has **no
  `tests/evals/` step** (verified). The PRD claim that "`just test-evals` validates that prompt changes
  or model swaps don't regress quality" is not true as built; the command errors out.
- **The groundedness gate marketed as the compliance centrepiece can't fire.** F8's R-QG-1 requires
  every `Finding` to cite ≥1 evidence_ref — but findings are constructed with `evidence_refs=(source,)`
  *unconditionally* (`sre_investigation.py:639-647`), so the check (`groundedness.py:83`) is vacuously
  satisfied on every run, and the root-cause prose itself is never checked. (The separate evidence
  *floor* — confidence capped at 0.3 with no sources, `sre_investigation.py:96,753-769` — IS real and
  does force approval; that part works.)

**Consequence:** none of the PRD's headline success criteria — "high-confidence investigations accurate
in 90%+ of cases," "60% MTTI reduction," etc. — are *measurable* with what exists. Only the support
feedback API (accept/reject rates) is a genuine signal. This connects to §0: elaborate scaffolding
(weighted multi-factor confidence, an eval "framework") around an unmeasured core.

### 1.3 Prompt injection into a tool-using agent, with fail-open guardrails

*Survives reframe: YES — the free-loop build must solve this too; its read-only bash guardrail is the right start.*

Untrusted content flows verbatim into prompts: alert `title`/`description`/`service` from
unauthenticated webhooks (`alert_classifier.j2`, `investigator.j2`, `k8s_investigator.py:65`);
Jira ticket text (`ticket_reviewer.j2`, `response_drafter.j2`); and **fetched docs** from
Notion/Confluence/S3 injected raw into the drafter (`response_drafter.j2:30-45`) — classic indirect
/ second-order injection. The **only** injection defence is the `<runbook>…</runbook>` quarantine
frame around matched runbook bodies. Nothing delimits or sanitizes alert, ticket, log, or doc content.

The agent's steered output can trigger Slack posts, PagerDuty notes, Jira replies, and tool calls.
The tool allowlist (`RunbookScopedToolset`, `plugins/toolsets/_runbook_scope.py`) has two holes:
it **fails open when no runbook matched** (`runbook is None` → pass-through, lines ~86-91) — and the
no-match/generic-exploration path is a supported flow — and it is **not applied to external MCP
toolsets at all** (`plugins/toolsets/mcp.py` wraps only for replay). If a kubectl MCP server is
configured (`K8S_MCP_SERVER_URL`), its tools — including any mutating verbs — are unscoped and
unbudgeted, driven by untrusted input, on the common no-runbook path. The kagent backend already
turns an unauthenticated webhook into a `create_namespaced_custom_object` CRD write
(`kagent_adapter.py:219`).

### 1.4 Nothing has been validated against real data

*Survives reframe: YES — and the production build is the only thing that has been.*

The PRD is candid that "all development has been against synthetic data … unvalidated against real
PagerDuty alerts, Jira tickets, and incident data." Combined with §1.2 (mocked evals, no CI gate),
**nothing in this repo has confirmed the core value proposition works on a real incident.** The only
real-world signal that exists is the production learning in §0 — and it said the loop needed rebuilding.
Treat every accuracy/latency claim in the PRD as a hypothesis, not a result.

### 1.5 The documented architecture is not the code that runs by default

*Survives reframe: YES — this is about docs telling you the wrong thing.*

README, `architecture.md`, and `CLAUDE.md` all state the SRE pipeline **runs on LangGraph**, and that
the Pydantic Graph implementation is **archived, "reference-only," and import-linter-forbidden**. The
code says otherwise. `worker.py:46` imports `from sentinel.interfaces.graphs._archive import investigation`,
`langgraph_sre_enabled` **defaults to `False`** (`settings.py:56`), and the dispatch at `worker.py:283-311`
takes the LangGraph branch *only* when the flag is on — so the **default execution path on `main` is the
archived pipeline** (verified). The "reference-only, forbidden" archive is both imported by production code
and is what actually runs.

Two consequences:
- **Docs-as-territory failure:** the single most important architectural claim in three top-level docs
  describes a non-default code path. Anyone (human or agent) reasoning from the docs reasons about the
  wrong pipeline.
- **Caveat on this review:** the confidence, groundedness, and evidence-floor analysis in §1.2 was verified
  against the LangGraph module (`sre_investigation.py`) — the **flag-on** path. The default path is the
  archived `investigation.py`; its behaviour on those same points was **not** separately audited here.
  Whichever pipeline you intend to ship, make the flag default match it and fix the docs.

---

## §2 — MEDIUM-RISK blind spots

### 2.1 Throughput ceiling breaks the MTTI SLO under exactly the load the product targets

*Survives reframe: PARTIAL (depends on new execution model).*

The worker claims **one job per poll and processes serially** (`worker.py:472-485`: `claim_next_job`
→ run → `asyncio.sleep(poll_interval)`); there is no semaphore or batch. Effective concurrency = worker
replica count (Helm default 2). In an alert storm — the *alert fatigue* scenario the product exists to
solve — 50 alerts at ~90s each drain in ~35+ minutes on 2 workers, blowing the "<2 min MTTI" success
criterion precisely when it matters most. There is no per-tenant or per-cost cap. (The queue itself is
sound — `SELECT … FOR UPDATE SKIP LOCKED`, verified — so this is a scaling-policy gap, not a correctness
bug.) Compounded by §1.1: unauthenticated + un-rate-limited manual triggers make cost-amplification/DoS
trivial.

**Worse: the Support pipeline isn't queued at all.** The Jira webhook runs the full LLM review
*synchronously inline in the API request* — `await workflows_support_review.review_ticket(...)`
(`support/router.py:165`, whose docstring says "inline (rather than enqueuing a job)"). So a ticket burst
runs unbounded concurrent LLM pipelines **in the API event loop**, starving `/health` and the SRE webhook
receivers that share the process. The PRD's "PostgreSQL-backed job queue … for safe multi-replica
processing" is true for SRE but bypassed for Support ingress (verified).

### 2.2 Deduplication is "crash on duplicate," not graceful skip

*Survives reframe: YES (webhook ingress persists).*

`enqueue_job` computes `idempotency_key = sha256(f"{job_type}:{source_id}")` and there **is** a UNIQUE
constraint (`uq_job_requests_idempotency_key`, migration 001 / `data/sql/jobs.py:20`). But the insert
path has **no `on_conflict` and no `IntegrityError` handling** (`domain/jobs/operations.py:24-78`), and
`_enqueue_alert` doesn't wrap it (`sre/router.py:47`). So a duplicate webhook raises rather than being
skipped — the opposite of graceful dedup, and webhook senders retry on 5xx. Worth checking whether
PagerDuty `incident.triggered` and `incident.escalated` collide on the same `source_id` (which would
silently drop escalations). Note: `CLAUDE.md`/README describe `_handle_webhook()` as the dedup point,
but that function does no dedup — the idempotency key is the only mechanism, and it fails ungracefully.

Two more edges (verified): the dedup is **permanent, not windowed** — `source_id` is the PagerDuty
incident id (`webhooks/pagerduty.py:42,62`), so a re-fired or flapping incident can *never* be investigated
again, ever. And the **Support webhook bypasses dedup entirely** (it runs inline, never enqueues), so Jira's
own delivery retries spawn concurrent duplicate reviews.

### 2.3 The QA gate mislabels failures, and `main` may be red on its own gates

*Survives reframe: YES (tooling/CI hygiene).*

`scripts/run-qa.sh:11` wraps `uv run … --check` in `if ! …; then` and prints a **hard-coded**
"Runbook content_sha drift detected" for *any* non-zero exit — real drift, a script error, or `uv`
simply not being on PATH (exit 127). Lines 17/23 do the same for `just lint` / `just test`. This
already cost a wrong-root-cause chase during this very review (a missing-`uv` failure was first
diagnosed as content drift, then walked back). A gate that reports the wrong cause is worse than no gate.

The signals below were initially **UNCONFIRMED** (no `uv`/venv in the review shells); a teammate later
provisioned a correct toolchain (`uv` + `just` + a **Python-3.13** `.venv` — the default resolved to 3.14,
which has no psycopg binary wheel) and ran the full gate, which **resolves it** (see the RESOLVED bullet):
- Over the review, the gate cycled through **three distinct failure classes on successive hook fires**:
  `content_sha` drift → lint → unit tests, with the repo `HEAD` unchanged and no writes from the sweeps.
- **This progression is itself the diagnosis.** `run-qa.sh` runs `set -e` and exits at the *first*
  failing check (sha, then lint, then unit, in that order). For the reported failure to *advance* from
  sha to lint to unit across fires, the earlier checks must have *passed* on the later runs. That is
  only possible if the hook **environment is changing between fires** — the "sha drift" was almost
  certainly `uv`-not-found (exit 127) on an early fire (matching one sweep's account), and once `uv` was
  present the sha check passed (⇒ **no real `content_sha` drift**), exposing a real-or-flaky `just lint`
  failure, then a `just test` failure. Net: the earlier "runbook content_sha drift on `main`" reading is
  most likely a **red herring** from an inconsistent hook environment.
- **RESOLVED — first-hand run (definitive).** With a correct toolchain: sha `--check` **PASS** (no real
  runbook drift — the earlier "drift" was `uv`-missing → exit 127, mislabeled by the gate), `just lint`
  **PASS**, `just test` = **1226 passed / 41 skipped / 1 FAILED**. `main` is red on exactly **one stale
  test**: `tests/unit/test_bootstrap_otel.py::TestInitTraces::test_no_op_when_no_endpoint`, which still
  asserts the old log event `otel.traces.disabled` after `bootstrap_otel.py` was refactored to emit
  `otel.traces.no_backend` (commit `0e48693`, 2026-05-02). `main` (HEAD `9a4a1a5`) has been red on this
  single assertion since **May 2**. So: not "broadly red" — one trivial outdated test, with lint and sha
  clean. A one-line fix (update the asserted event name) would green the suite; given §8 (legacy) it isn't
  worth doing unless you want a green portfolio artifact. Minor aside: the `.venv` defaulting to Python 3.14
  (no psycopg wheel) is a real dev-onboarding trap, moot for a legacy repo.
- Related PRD overstatement: R-RB-1 (`prd.md:197`) claims "CI re-derives + asserts equality (fail-closed)."
  The `content_sha` check is **not in `ci.yml`** — it lives only in `run-qa.sh` and `.pre-commit-config.yaml`,
  so drift *can* reach `main` without CI catching it. That is consistent with the drift being real.

### 2.6 Features sold in the docs that are dead or stubbed in code

*Survives reframe: YES (docs credibility).*

- **A/B comparison mode is a no-op.** `K8S_INVESTIGATION_BACKEND=both` is treated identically to `native`
  (`config.py:264` — `if backend in ("native", "both")`), so "both" does **not** run both backends. Real
  A/B lives behind a separate `CHALLENGER_ADAPTER` builder, and even that is wired only into the archived
  pipeline, not the LangGraph one. `ComparisonResult` is computed but `persist_comparison_run` has **zero
  callers** (verified) — the `comparison_runs` table is dead, and the comparison zeroes out all quality
  dimensions, comparing only latency/source-count/diversity. The PRD (`prd.md:320`) and architecture sell
  "config-driven A/B comparison mode" as a headline capability.
- **HolmesGPT is not wireable as documented.** The `holmesgpt_enabled` setting is **commented out**
  (`settings.py:46`), so the `HOLMESGPT_ENABLED=true` toggle the README advertises does not exist. The
  adapter module and a builder reference remain (`domain/investigations/holmes_adapter.py`,
  `plugins/common/config.py`), but there is no live enable path. README/PRD (`prd.md:217,283`) still
  present it as an opt-in investigation engine.

### 2.4 Compliance machinery outruns the single-team reality

*Survives reframe: NO for most of it — the free-loop build is the moment to shed this.*

The PRD states V1 is **single-team, single-tenant** (`prd.md:48`), yet the repo ships F7 capability
tokens, tenant-scope enforcement, cross-tenant rejection, and MNPI handling. This is the over-
engineering the §0 reframe flags from another angle: substantial complexity solving problems the
deployment doesn't have yet. The `tenant_id` it enforces on is itself derived from the *unauthenticated*
webhook payload (`envelope_factory`), so an attacker can assert any tenant — meaning the isolation is
not a real security boundary today regardless.

### 2.5 Dual-pipeline drift and a stalled migration

*Survives reframe: NO if the repo is retired; otherwise a live maintenance tax.*

SRE runs on LangGraph (flag-gated `LANGGRAPH_SRE_ENABLED`), support on Pydantic Graph, legacy SRE
archived. The umbrella LangGraph migration is ~67% done (INDEX.md: T21–T30 cleanup outstanding). Two
orchestration frameworks, a feature flag, and an archived third copy is a lot of surface to keep behaviourally
in sync, and the flag means two code paths must both be tested. If §0's decision retires the repo, don't
finish the migration — stop investing.

Sharper split-brain edge (verified): the Support ingress paths run **different engines**. The Jira webhook
runs the **LangGraph** support graph inline (`support/router.py:21,165`), while the manual `/support/review`
endpoint enqueues to the worker, which runs the **Pydantic Graph** support pipeline (`worker.py:45,408`).
Two engines behind one product surface, chosen by entry point — a latent correctness/behaviour-drift trap
(e.g. an approval-gated review behaves differently, or can't be resumed, depending on how it was triggered).

### 2.7 Reliability defects in the queue/approval machinery (verified late in review)

*Survives reframe: PARTIAL — the queue dies with the repo, but every one of these is a lesson for the successor.*

These were confirmed first-hand after a teammate provisioned a working toolchain; they are the most severe
operational gaps found, and several directly undercut PRD claims:

- **The human-approval gate notifies nobody (critical).** `wait_for_human` only calls LangGraph `interrupt()`
  (`sre_investigation.py:862`). The Slack Approve/Reject sender `post_approval_request` exists
  (`vendors/slack/__init__.py`) but has **zero call sites** (verified). So a low-confidence investigation
  pauses silently and waits indefinitely with **no human aware**. The compliance centrepiece — human sign-off
  before publishing — is invisible in practice. (Compounds §1.1/§1.2.)
- **`APPROVAL_TIMEOUT_SECONDS` is dead.** Default 0, and **no code reads it** (verified) — no sweeper, no
  auto-approve. Pending approvals accumulate forever, and the `AsyncPostgresSaver` checkpoints have no TTL or
  cleanup, so the checkpoint table grows unbounded.
- **Stale-job recovery is broken for the exact case it names.** `recover_stale_jobs` re-queues
  `WHERE status='running' AND locked_by=worker_id` (`domain/jobs/operations.py:363-367`), called on startup
  with `worker_id = HOSTNAME`. In a K8s Deployment a crashed pod is replaced with a **new** hostname, so its
  in-flight `running` jobs are never reclaimed (claim reads only `pending`); `locked_at` is written but no
  time-based reaper reads it. The PRD's "stale job recovery for workers that crash mid-investigation"
  (`prd.md:112`) does not hold across pod replacement.
- **Outbound side effects are not idempotent.** Slack/PagerDuty posts carry no idempotency key. A timeout or
  crash *after* `publish_findings` posts but *before* the job is marked complete causes a retry to re-run the
  whole graph → **duplicate Slack messages and PagerDuty notes**. Publish uses `gather(return_exceptions=True)`
  and only logs failures.
- **The worker never initialises metrics.** `worker._main` calls `bootstrap.initialise()` (traces only) and
  never `init_meters` / starts a metrics server (`worker.py:543`; no metrics port bound). So *every*
  `metrics.record_*` no-ops in the process that runs **all** investigations — queue depth, job counts, and
  approval decisions are never measured. (Deepens §3.1.)
- **Inconsistent failure handling.** `classify_alert` re-raises (job fails → retried), but `investigate` and
  `analyse_root_cause` swallow exceptions and emit a low-confidence fallback (`sre_investigation.py:477-485,
  605-626`), so the job "completes" with a bogus result instead of failing. Retries re-run the entire pipeline
  from scratch (fresh LLM calls, up to 3×) — cost amplification concentrated on the alerts that are already
  misbehaving.

---

## §3 — DEFERRABLE blind spots

- **3.1 Sentinel can't observe its own failures well.** LLM-call metrics (`sentinel_llm_calls_total`)
  and approval-decision metrics are *declared but never invoked* (PRD §4 admits this). The Langfuse
  mandatory-attribute validator warns but **does not drop** spans, and unset `LANGFUSE_HOST` silently
  falls back — a prod misconfig loses traces quietly. If an investigation dies mid-run, an operator's
  main signal is structlog. *Survives reframe: YES.*
- **3.2 Unbounded data growth.** No retention/TTL on `investigation_records`, the LangGraph checkpointer
  table, `audit_log`, or `replay_bundle_json` blobs. Fine at synthetic scale; a silent cost/latency
  cliff in prod. *Survives reframe: YES.*
- **3.3 Info disclosure.** OpenAPI `/docs` (with `/` redirecting to it) and Prometheus `/metrics` are
  served unauthenticated (`app.py:78,91`). *Survives reframe: YES.*
- **3.4 Query injection into observability backends.** Webhook-controlled `service` is interpolated raw
  into LogQL/PromQL/TraceQL and Datadog queries (`observability/grafana.py:182-190`, `datadog.py:250-256`).
  Read-only, but enables cross-service/tenant data reads and expensive-query DoS. *Survives reframe: YES.*
- **3.5 Secret hygiene.** Several secrets are plain `str` not `SecretStr` (`settings.py` PD/DD/Grafana/MCP/
  Jira/Notion), so a `Settings` repr/dump would leak them; no evidence of such a dump today. *Survives reframe: YES.*
- **3.6 Doc-vs-code drift (onboarding friction).** `CLAUDE.md` instructs reading `graphify-out/GRAPH_REPORT.md`
  — the file (and directory) **does not exist**. `architecture.md` "Key Reference Files" points to
  `interfaces/graphs/investigation.py` (**archived** to `_archive/`) and to `domain/pipeline/types.py` for
  ReplayBundle (the live one is `utils/replay_bundle.py`). The README SRE diagram omits the `assess_quality`
  node. Small individually, but these are the exact pointers a new engineer (or agent) is told to trust
  first. *Survives reframe: N/A (docs).*
- **3.7 Test count understates volume but overstates coverage.** Docs cite "695+/770+ tests"; the actual
  count is ~1,584 test functions. But the extra volume is low-signal for the core question: all
  functional/pipeline tests fully mock the agents (`functional/conftest.py:62-194`), CI runs only
  unit+integration, and prompt *user* blocks (where untrusted content lands) are not render-tested.
  A big green test number is not evidence the agent works. *Survives reframe: PARTIAL.*

---

## §4 — Default assumptions that could be wrong (question 2)

1. **"Structured orchestration makes the agent trustworthy."** Production says it makes it *dumber*. (§0)
2. **"Multi-factor confidence is calibrated."** It's LLM-self-report + a constant. (§1.2)
3. **"The eval framework guards quality."** It mocks the model and the judge and isn't in CI. (§1.2)
4. **"Runbook scoping contains the agent's tools."** It fails open and skips MCP tools. (§1.3)
5. **"Webhook dedup protects us from duplicates."** It raises on duplicates instead. (§2.2)
6. **"`tenant_id` is a security boundary."** It's attacker-assertable from an unauthenticated payload. (§2.4)
7. **"Vendor no-op-when-unconfigured is safe."** CONFIRMED false-comfort: with Slack unconfigured
   `post_investigation_summary` logs `slack_post_skipped` and returns; if PagerDuty is also unconfigured,
   `publish_findings` still logs `investigation_completed` and returns `findings_published=True`
   (`sre_investigation.py:978-993`) — findings go nowhere while the system reports success. Worth an explicit
   "dropped output" signal.
8. **"<2 min MTTI is achievable."** Not under storm with serial workers. (§2.1)
9. **"The docs describe what runs."** The default SRE pipeline is the *archived* one; the docs describe
   the flag-off-by-default LangGraph path. (§1.5)
10. **"A/B comparison mode compares backends."** `both` == `native`; the comparison table is dead. (§2.6)
11. **"HolmesGPT is an available engine."** Its enable flag is commented out. (§2.6)

---

## §5 — What would a reviewer / investor / user / tech lead ask? (question 6)

- **Investor:** "You've built a hedge-fund-compliance agent with no authentication and an approval gate
  anyone can satisfy under a fake name. What is the compliance moat actually made of?" And: "Your own
  production run replaced the core engine — what's the asset here, the framework or the learnings?"
- **Security reviewer:** "Unauthenticated webhook → LLM tool loop → (fail-open) cluster access + outbound
  posts, with raw untrusted text in the prompt. Walk me through why this isn't RCE-adjacent when a kubectl
  MCP server is attached."
- **On-call user (the actual customer):** "It posts a confident root cause — how often is it right? If the
  confidence number is the model grading itself and you've never measured accuracy on a real incident, why
  should I trust the summary over my own triage?"
- **Tech lead:** "Two orchestration frameworks, a stalled migration, F6/F7 machinery for a single tenant,
  and a determinism test suite — and production threw the orchestration out. What do we delete this quarter?"
- **Risk officer:** "Show me who approved investigation X." (Today: a free-text string the caller typed.)

---

## §6 — Recommended clarifications, in priority order

These are the decisions that change project direction (question 4). Resolve top-down.

1. **Repo disposition (blocks everything):** Is this codebase legacy vs the Claude Agent SDK build, or
   does it still ship? Pick one and prune accordingly.
2. **Define "compliance" concretely:** which do you actually need — (a) audit + human approval +
   read-only enforcement (keep; wraps any loop), or (b) deterministic replay + capability tokens +
   tenant isolation (likely shed for a single-team tool)? §0 argues (a), not (b).
3. **Authentication + accountable approver identity** — non-negotiable floor before any deployment,
   regardless of #1. (§1.1)
4. **Measure the core once, for real:** run a handful of *real* incidents through *unmocked* agents with a
   human judging accuracy. Until then the value prop is unproven. (§1.2, §1.4)
5. **Standardise the guardrail on the production shape:** read-only at the tool boundary, always on,
   fail-closed. Retire fail-open runbook scoping. (§1.3, §0)

---

## §7 — Credit where due (so this review is honest about strengths)

- The **SRE job-queue core is genuinely solid**: Postgres `FOR UPDATE SKIP LOCKED`, real `asyncio` job
  timeouts, and retry-with-max-attempts (`worker.py:470-497`) — better than the naive `BackgroundTasks`
  approach many projects ship. (Caveat: stale-job *recovery* is real but worker-id-scoped, so it misses
  crashed-and-replaced pods — §2.7; and this praise is SRE-only, since Support bypasses the queue — §2.1.)
- **LangGraph `interrupt()` + `AsyncPostgresSaver`** genuinely lets an approval survive a worker restart —
  the archived Pydantic Graph path could not. If the repo lives, this is the right primitive.
- The **support feedback API** is a real quality signal (unlike the confidence/eval loops).
- The **runbook body quarantine frame** is a correct, if narrow, injection defence — the instinct is right;
  it just needs to extend to all untrusted content (§1.3).

The engineering craft is high throughout. The blind spots are not sloppiness — they are the predictable
result of building a large deterministic *map* ahead of ever walking the *territory* with real data and a
real model. Production walked it, and reported back. This document exists to make that report load-bearing.

---

## §8 — Disposition (decided 2026-07-06): this repo is LEGACY

The governing question in §0 is resolved: **this PydanticAI/LangGraph codebase is legacy, superseded by the
Claude Agent SDK production build.** That collapses the review into three buckets. Most "fixes" are no longer
worth doing *here* — the point is to harvest the lessons into the successor and stop spending on the map.

### 8.1 Stop investing — close out, don't fix

None of these should get another hour in this repo:
- **Finish nothing.** Abandon the in-progress plans: `langgraph-sre-migration` T21–T30 cleanup,
  `metrics-and-observability-wiring`, `pydanticai-langgraph-adoption`, and any F6/F7 follow-ons. Mark them
  **abandoned** in `docs/plans/INDEX.md`.
- **Don't green `main`.** `main` is red on exactly one stale test (§2.3, confirmed by a first-hand run;
  lint + sha are clean). It's a one-line fix, but a legacy repo being red is not worth the churn. Do not
  chase it or rebake runbook shas. The one earlier "urgent" open item is downgraded to *ignore*.
- **Don't fix the dead/mislabeled features.** A/B comparison (§2.6), HolmesGPT toggle (§2.6), broken evals
  (§1.2), archived-vs-LangGraph default (§1.5), doc-drift (§3.6) — all moot. Leave them.
- **Don't repair the confidence/groundedness machinery** (§1.2). It was the wrong design; it dies with the repo.

### 8.2 Harvest into the Claude Agent SDK build — the actual deliverable of this review

These are the transferable lessons; port the *principle*, not the code:
- **Auth + accountable approver identity (§1.1)** — the successor needs authenticated ingress and a real,
  non-self-asserted approver on any human gate. This was the biggest gap and it does not solve itself in a
  new framework.
- **Guardrail shape (§1.3, §0)** — you already got this right in production: read-only at the tool boundary,
  always-on, fail-closed. Keep it that way; do not reintroduce per-runbook fail-open scoping.
- **Measure the core on real data (§1.2, §1.4)** — carry forward a *thin* eval that runs unmocked agents on
  real incidents with a human/LLM judge, and gate prompt/model changes on it in CI. This is the one piece of
  the "quality loop" worth rebuilding — done right this time.
- **Compliance is separable from orchestration (§0)** — wrap the free agent loop with an append-only audit
  log, a human approval gate, and per-call trace/token capture. Drop deterministic replay and capability
  tokens.
- **Prompt-injection hygiene (§1.3)** — untrusted alert/ticket/log/doc content still flows into prompts in
  the new build; keep the quarantine-frame instinct and extend it to all untrusted content, not just runbooks.
- **Reliability lessons from §2.7 (the queue dies, the mistakes shouldn't repeat):**
  - **Actually notify the approver.** The human gate is worthless if nothing pings a human — wire the
    Slack/whatever notification into the pause path from day one (the bug here was a `wait_for_human` that
    interrupted but never sent).
  - **Reap stale work by time, not by worker identity** — key recovery on `locked_at` age so a
    crashed-and-replaced pod's in-flight work is reclaimed.
  - **Never run the agent loop inline in the request handler** — always hand off to a worker/queue so an
    ingress burst can't starve health checks and other webhooks (the Support-inline mistake, §2.1).
  - **Make outbound posts idempotent** — dedup Slack/PagerDuty/Jira sends by a stable key so a retry or
    restart near the publish boundary can't double-notify on-call (§2.7).
  - **Initialise metrics in every process that does work**, not just the API — and emit queue depth
    (§2.7/§3.1).

### 8.3 The one branch that still needs your call

**Is this legacy repo still deployed / receiving real webhooks anywhere, or is it fully retired?**
- **If retired / never deployed to real traffic** (most likely — the PRD says production is a separate repo
  and all dev was against synthetic data): the §1 security findings are **archival**, no action needed.
- **If any instance still serves real PagerDuty/Datadog/Jira webhooks:** §1.1 (no auth), §1.3 (injection →
  fail-open tools), §3.4 (query injection), and §2.1 (unauthenticated cost-DoS) are **live exposure**. Put
  authentication in front of it or take it down — do not leave an unauthenticated agent with cluster access
  reachable just because the repo is "legacy."

### 8.4 Cheap legacy hygiene (optional, ~30 min)

So no future reader or agent mistakes this for active:
- Add a **LEGACY banner** to `README.md` pointing at the successor build.
- Mark the in-progress plans **abandoned** in `docs/plans/INDEX.md` (§8.1).
- Consider archiving the GitHub repo (read-only) and/or freezing the branch.
- Fix or delete the `CLAUDE.md` graphify pointer and the stale `architecture.md` reference paths (§3.6) —
  or don't, if archiving makes it moot.
