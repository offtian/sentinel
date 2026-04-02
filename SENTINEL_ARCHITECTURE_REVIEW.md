# Sentinel Multi-Agent Architecture Review

> **FROZEN DOCUMENT** — This review is a point-in-time snapshot from 2026-04-01.
> It is preserved for historical context. Do not update the resolution table.
> Current implementation status is tracked in `docs/prd.md` (acceptance criteria).

**Reviewer**: Staff MLE, Platform Engineering
**Date**: 2026-04-01
**Scope**: Architecture evaluation, gap analysis, eval framework design

---

## Implementation Response Log

> This section tracks the team's response to each finding. Items are marked:
> - **RESOLVED (pre-review)** — was already fixed before the review was written (reviewer worked from a stale snapshot)
> - **RESOLVED (post-review)** — implemented in direct response to this review
> - **ACKNOWLEDGED** — valid finding, tracked for future work
> - **DIRECTION CHANGED** — we took a different approach than recommended

| # | Finding | Status | Resolution |
|---|---------|--------|------------|
| 3.1 | No multi-agent orchestration | **RESOLVED (post-review)** | Supervisor graph with quality gating, retry, and escalation (`application/supervisor/orchestrator.py`). Pipelines remain linear DAGs — supervisor wraps them rather than adding internal branching. |
| 3.2 | Confidence scoring is a stub | **RESOLVED (pre-review)** | `from_factors()` already existed with proper 0.3/0.5/0.2 weighting. Both pipelines use it. `from_total()` retained for backward compatibility only. |
| 3.3 | HolmesGPT is a TODO | **RESOLVED (pre-review)** | `DirectToolsetAdapter` queries Datadog logs/metrics/traces concurrently via vendor adapters, bypassing the HolmesGPT SDK. Production-ready. |
| 3.4 | Webhooks run pipelines synchronously | **RESOLVED (pre-review)** | Webhooks enqueue jobs via `enqueue.enqueue_investigation()`, return 202 Accepted. PostgreSQL job queue with worker replicas. |
| 3.5 | State mutation inside nodes | **ACKNOWLEDGED** | `model_copy(update={...})` is used consistently. Full event-sourcing deferred — current audit log captures decisions but not intermediate state snapshots. |
| 3.6 | No error handling in pipeline nodes | **RESOLVED (post-review)** | All nodes wrapped with try/except. Critical nodes (classify) fail pipeline cleanly. Degradable nodes (Holmes, RCA, search) continue with partial results. `PublishFindings` uses `gather(return_exceptions=True)`. |
| 3.7 | Config module anti-pattern | **RESOLVED (pre-review)** | `settings.py` uses `pydantic_settings.BaseSettings` with `SettingsConfigDict`. `_config.py` replaced by `config.py` wiring layer. |
| 3.8 | Duplicated webhook logic | **RESOLVED (post-review)** | Extracted `_handle_webhook()` shared handler. PD and Datadog endpoints are thin wrappers passing parse function and source name. |
| 4.1 | No supervisor agent | **RESOLVED (post-review)** | See 3.1. Rule-based quality gate (`domain/supervisor/quality_gate.py`) + orchestrator with PUBLISH/RETRY/ESCALATE/REJECT decisions. |
| 4.2 | No human sign-off | **RESOLVED (post-review)** | Approval gate in `DetermineConfidence` node. Slack interactive messages with approve/reject buttons. API endpoints for approve/reject/status. Configurable confidence threshold. |
| 4.3 | No coding agent | **ACKNOWLEDGED** | Future sprint. Runbook/monitor generation from investigation findings. |
| 4.4 | No customer service agent | **ACKNOWLEDGED** | Future sprint. Trader-facing Slack interactions. |
| 4.5 | No intent router | **RESOLVED (pre-review)** | `interfaces/graphs/agents/intent_router.py` with PydanticAI agent. `interfaces/chat/app.py` routes to SRE or support pipeline based on classification. |
| 5.x | No eval framework | **DIRECTION CHANGED** | Built on `pydantic_evals` library (not custom protocol). Composable evaluators (keyword coverage, structural checks) attached per case. Rich console rendering. See Section 5 response below. |
| 6.1 | Wire webhooks to job queue | **RESOLVED (pre-review)** | See 3.4. |
| 6.2 | Implement `from_factors()` | **RESOLVED (pre-review)** | See 3.2. |
| 6.3 | Add error handling | **RESOLVED (post-review)** | See 3.6. |
| 6.4 | Build DirectToolsetAdapter | **RESOLVED (pre-review)** | See 3.3. |
| 6.5 | Build intent router | **RESOLVED (pre-review)** | See 4.5. |
| 6.6 | Tier 2 eval framework | **RESOLVED (post-review)** | See 5.x. |
| 6.7 | Supervisor graph | **RESOLVED (post-review)** | See 4.1. |
| 6.8 | Human sign-off | **RESOLVED (post-review)** | See 4.2. |

---

## 1. Overall Rating: 7.2 / 10

Sentinel is a well-structured codebase with strong engineering fundamentals. The clean architecture, type safety, and vendor abstraction put it ahead of most internal tooling I've seen at hedge funds. But it's currently a **two-pipeline system masquerading as a multi-agent platform**. The gap between "what's built" and "production multi-agent orchestration for a trading floor" is significant.

---

## 2. What's Good (Keep These)

### 2.1 Layered Architecture with Enforcement

The import-linter contracts in `pyproject.toml` are the right call. Most teams *say* they have clean architecture but violate layer boundaries within weeks. Enforcing `interfaces → application → domain → data` at CI is exactly how you prevent rot. The fact that vendor adapters can't be imported into domain operations (forcing DI) shows real discipline.

### 2.2 Pydantic Graph as Pipeline Orchestrator

Using `pydantic_graph.Graph` with typed `BaseNode[State, Dependencies, Reply]` is a strong choice over LangGraph or raw chains. The state machine semantics are explicit — each node declares its successor type at compile time (`ClassifyAlert → InvestigateWithHolmes | End[...]`). This makes dead-code analysis and type checking possible, which LangGraph can't do.

The pattern of frozen `Dependencies` dataclass with DI + mutable `State` that uses `model_copy(update={...})` for immutable transitions is clean. You avoid the "god context" anti-pattern that plagues most agent frameworks.

### 2.3 Vendor Adapter Abstraction

`BaseHolmesAdapter`, `BaseDocumentSearcher`, `BasePastTicketSearcher` — these ABCs with `is_configured` no-op semantics mean you can deploy without all integrations wired up. This is critical for a hedge fund where you might have Datadog in one fund but Grafana in another.

### 2.4 Structured Logging Convention

Enforcing `structlog` via import-linter (stdlib `logging` is forbidden) and using `logs.log_event("event_name", params={...})` consistently means every agent node emits queryable events. In a platform engineering context, this is table stakes but rarely done well.

### 2.5 Async-First with Proper Concurrency

`asyncio.TaskGroup()` for parallel search (doc + ticket search in support pipeline), `asyncio.gather()` for publishing to Slack + PagerDuty + DB — you're not leaving latency on the table. The `TaskGroup` usage in `SearchDocumentation` is particularly well-structured.

---

## 3. What's Bad (Fix These)

### 3.1 No Real Multi-Agent Orchestration — Just Sequential Pipelines

**Severity: Critical** | **Status: RESOLVED (post-review)**

Both graphs (`sre_investigation.py`, `support_review.py`) are **linear DAGs**, not agent networks. The SRE pipeline is literally:

```
ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → PublishFindings
```

There's no conditional branching (except the trivial "no search results → early exit" in support), no agent-to-agent delegation, no feedback loops, and no supervisor oversight. In a real incident, the root cause analyser might need to request *additional* Holmes investigation (loopback), or the confidence score might be too low and trigger a different investigation strategy.

**What you have**: Orchestrated pipelines with LLM nodes.
**What you need**: Agents that can delegate, retry with different strategies, escalate, and coordinate.

> **Response:** We implemented a supervisor layer (`application/supervisor/orchestrator.py`) that wraps both pipelines with quality gating. The supervisor evaluates output quality via rule-based checks (`domain/supervisor/quality_gate.py`) and makes PUBLISH/RETRY/ESCALATE/REJECT decisions. On retry, side effects (Slack, PagerDuty) are suppressed. The pipelines themselves remain linear DAGs — this is intentional. Linear DAGs are easier to debug, trace, and audit than cyclic graphs. The supervisor provides the retry/escalate logic externally rather than embedding it in the graph topology.
>
> Internal loopback (e.g., RCA requesting more Holmes data) remains a future consideration — it would require changing the Pydantic Graph node return types to allow backward edges, which the current library doesn't support cleanly.

### 3.2 Confidence Scoring is a Stub

**Severity: High** | **Status: RESOLVED (pre-review) — reviewer worked from stale snapshot**

> **Response:** This finding was based on a stale codebase snapshot. `ConfidenceScore.from_factors()` exists at `domain/confidence/entities.py:49-99` and independently scores source count (30%), relevance (50%), and recency (20%). Both pipelines call `from_factors()` in their `DetermineConfidence` nodes. `from_total()` is retained only for backward compatibility and test convenience — it is not used in any pipeline path. Functional tests assert against the computed multi-factor total (e.g., `0.3*(2/5) + 0.5*0.85 + 0.2*0.8 = 0.705`).

### 3.3 HolmesGPT is a TODO

**Severity: High** | **Status: RESOLVED (pre-review) — reviewer worked from stale snapshot**

> **Response:** The `HolmesAdapter` stub described above still exists as a fallback, but production uses `DirectToolsetAdapter` (`domain/sre/holmes_adapter.py:105-256`). This adapter queries Datadog logs, metrics, and traces concurrently via the `BaseObservabilityClient` interface, with circuit breaker protection. The HolmesGPT SDK dependency conflict remains unresolved, but `DirectToolsetAdapter` provides equivalent observability data without the SDK.

### 3.4 Webhook Handlers Run Pipelines Synchronously

**Severity: High** | **Status: RESOLVED (pre-review) — reviewer worked from stale snapshot**

> **Response:** Webhooks now enqueue jobs via `enqueue.enqueue_investigation()` and return 202 Accepted immediately. The PostgreSQL job queue (`SELECT ... FOR UPDATE SKIP LOCKED`) supports horizontal scaling via worker replicas. The `application/` layer contains enqueue, dequeue, persist, and automation modules. Job retry (up to 3 attempts), timeout (300s), and stale job recovery are all implemented.

### 3.5 State Mutation Inside Nodes

**Severity: Medium** | **Status: ACKNOWLEDGED — partial mitigation**

> **Response:** Valid observation. `model_copy(update={...})` creates new Pydantic model instances (not in-place mutation), but the `ctx.state` reference is reassigned. Full event-sourced state is deferred. Current mitigation: the append-only audit log (`domain/audit/entities.py`) records each agent decision with SHA-256 input hashes and model IDs. Error handling now ensures that if a node fails partway, the pipeline either degrades gracefully or exits cleanly rather than leaving state inconsistent.

### 3.6 No Error Handling in Pipeline Nodes

**Severity: Medium** | **Status: RESOLVED (post-review)**

> **Response:** All pipeline nodes now have structured error handling:
>
> - **Critical nodes** (`ClassifyAlert`, `ClassifyTicket`): catch exceptions, log via `logs.log_exception()`, return `End(reply)` with error context. Pipeline stops cleanly.
> - **Degradable nodes** (`InvestigateWithHolmes`, `AnalyseRootCause`, `SearchDocumentation`, `DraftResponse`): catch exceptions, log, continue with partial/fallback results. The investigation still reaches output channels with whatever data is available.
> - **`DetermineConfidence`**: catches exceptions, defaults to LOW (0.0) confidence — which triggers the human approval gate.
> - **`PublishFindings`**: uses `asyncio.gather(return_exceptions=True)` so a Slack outage doesn't block PagerDuty or database persistence.
>
> Retry at the pipeline level is handled by the supervisor orchestrator, not within individual nodes.

### 3.7 Config Module Anti-pattern

**Severity: Medium** | **Status: RESOLVED (pre-review) — reviewer worked from stale snapshot**

> **Response:** `settings.py` exists and uses `pydantic_settings.BaseSettings` with `SettingsConfigDict` for `.env` file loading and environment variable validation. A separate `config.py` wires vendor adapters and builders from settings via a `Configuration` class. The old `_config.py` is no longer the primary config path.

### 3.8 Duplicated Webhook Logic

**Severity: Low** | **Status: RESOLVED (post-review)**

> **Response:** Extracted `_handle_webhook(*, payload, parse_fn, source)` shared handler in `interfaces/api/routers/sre/router.py`. PagerDuty and Datadog endpoints are now 4-line wrappers passing their parse function and source name. The webhook parsers themselves remain separate (PagerDuty V3 and Datadog have fundamentally different payload structures).

---

## 4. What's Missing (Build These)

### 4.1 Supervisor Agent

**Status: RESOLVED (post-review)**

> **Response:** Implemented with a different topology than recommended. Rather than a `SupervisorGraph` that wraps everything as a single Pydantic Graph, we built:
>
> - **Quality gate** (`domain/supervisor/quality_gate.py`): Pure functions `evaluate_sre_quality()` and `evaluate_support_quality()` that check for None/empty/generic outputs, missing confidence, non-actionable remediation, and short/formulaic responses. Returns a `QualityVerdict` with score and issues list.
> - **Orchestrator** (`application/supervisor/orchestrator.py`): `supervise_sre_investigation()` and `supervise_support_review()` wrap the existing pipeline entry points. On quality failure, retries with suppressed side effects (Slack, PagerDuty), tracks the best result across attempts, and decides PUBLISH (quality passed), RETRY (quality failed, retries remain), ESCALATE (quality failed, score >= 0.3), or REJECT (quality failed, score < 0.3).
> - **Intent router** (`interfaces/graphs/agents/intent_router.py`): Already existed — PydanticAI agent classifying inbound messages into SRE or SUPPORT intent, used by the chat interface.
>
> The routing → dispatch → quality gate → retry/escalate flow the review described is implemented, but as composed functions rather than a single graph. This avoids coupling the supervisor lifecycle to the Pydantic Graph runtime and keeps each concern testable independently (26 quality gate unit tests, 6 orchestrator functional tests).

### 4.2 Human Sign-off Agent

**Status: RESOLVED (post-review)**

> **Response:** Implemented:
>
> - **Approval gate** in `DetermineConfidence` node: when confidence is below `require_approval_below_confidence` (default 0.7) and an approval callback is configured, the pipeline posts to Slack and returns `End(reply)` with `approval_status="pending"` instead of publishing.
> - **Slack interactive message** (`vendors/slack.py:post_approval_request()`): Block Kit message with "Approve & Publish" and "Reject" buttons, keyed by investigation ID.
> - **API endpoints**: `POST /api/sre/investigations/{id}/approve`, `POST /api/sre/investigations/{id}/reject`, `GET /api/sre/investigations/{id}/approval-status`.
> - **Domain entity** (`domain/approval/entities.py`): Frozen `ApprovalRequest` with `approve()`, `reject()`, `auto_approve()` transitions returning new instances (immutable).
> - **Auto-approve**: `approval_timeout_seconds` setting exists (default 0 = disabled). Implementation of the timer-based auto-approve is deferred to when the approval store moves to the database.
> - **Audit trail**: Append-only `AuditLogRecord` with SHA-256 input hashes, model IDs, and prompt versions. INSERT-only database permissions.

### 4.3 Coding Agent

**Status: ACKNOWLEDGED — future sprint**

### 4.4 Customer Service Agent (Trader/Quant-Facing)

**Status: ACKNOWLEDGED — future sprint**

### 4.5 Intent Router

**Status: RESOLVED (pre-review) — reviewer worked from stale snapshot**

> **Response:** `interfaces/graphs/agents/intent_router.py` contains a PydanticAI agent with `Intent` enum (SRE/SUPPORT) and system prompt loaded from `intent_router.j2`. `interfaces/chat/app.py` is a full Streamlit chat UI that calls `_classify_intent()` and routes to either `_run_sre()` or `_run_support()`. Unit tests exist at `tests/unit/interfaces/chat/test_intent_detection.py`.

---

## 5. Pluggable Eval Framework Design

**Status: DIRECTION CHANGED — built on `pydantic_evals` instead of custom protocol**

> **Response:** We adopted a different architecture than proposed. Rather than a custom `BaseEvaluator` protocol with `MetricKind`, `MetricSpec`, and registry decorators, we built on the `pydantic_evals` library. Key differences from the proposal:
>
> | Proposed | Implemented | Reasoning |
> |----------|-------------|-----------|
> | Custom `BaseEvaluator` ABC | `pydantic_evals.Dataset` + `pydantic_evals.evaluators.Evaluator` | Reuse battle-tested library rather than building from scratch |
> | `MetricKind` enum (HIGHER/LOWER/BINARY) | Evaluator returns assertions (pass/fail) and scores (0-1) | Simpler model — assertions for CI gating, scores for trending |
> | Registry decorator pattern | Dataset builder functions per agent type | Explicit > implicit — each agent's `load_cases()` returns a fully configured dataset |
> | Custom `EvalRunner` with regression check | `pydantic_evals.Dataset.evaluate()` + Rich console rendering | Library handles concurrency, timing, and result collection |
> | Separate `rubrics/` directories | Rubrics embedded in JSON golden cases | Fewer files, rubrics travel with their cases |
>
> **What's implemented:**
> - `evals/cases/base.py`: `Rubric` attrs type, JSON dataset loading, evaluator builder per agent returning `pydantic_evals.Dataset`
> - `evals/evaluators/keyword_coverage.py`: `pydantic_evals.Evaluator` subclass for keyword matching with configurable threshold
> - `evals/evaluators/structural.py`: `pydantic_evals.Evaluator` subclass for structural checks (non_empty, has_items, exact_match, gte) — serves the same role as the proposed `BINARY` metrics
> - `evals/runner.py`: `async run()` entry point replacing the proposed `EvalRunner` class
> - `evals/reporting.py`: `EvaluationReport` wrapper with `get_assertion_average()` and `get_score_average()`
> - `evals/rendering.py`: Rich console table output
> - 3 agent evaluators (alert_classifier, root_cause_analyser, response_drafter) with 5 golden cases each
>
> **What's deferred:**
> - LLM-as-judge evaluator (the `LLMJudge` proposed in 5.4) — interface is ready in `pydantic_evals`, needs a judge model configured
> - Regression checking against baselines — needs eval result persistence first
> - Tier 3 E2E evals with real LLM + real searchers

### 5.7 Three Eval Tiers

| Tier | Runs | What It Tests | LLM Calls? | CI Gate? | Status |
|------|------|---------------|------------|----------|--------|
| **Unit** | Every PR | Pipeline plumbing, node transitions, state shape | No (mocked agents) | Yes, must pass | **DONE** — `tests/unit/`, `tests/functional/` |
| **Component** | Nightly | Individual agent quality against golden datasets | Offline: no. Online: yes | Yes, flag regressions | **DONE (offline)** — `evals/` with rule-based scoring. Online (LLM judge) deferred. |
| **E2E** | Weekly / pre-release | Full pipeline with real LLM + real searchers (staging) | Yes | Advisory only | **NOT STARTED** — needs staging environment |

---

## 6. Priority-Ordered Recommendations

| # | Item | Effort | Impact | Do When | Status |
|---|------|--------|--------|---------|--------|
| 1 | Wire webhook handlers to job queue (async) | S | Critical — PD will timeout | This sprint | **DONE (pre-review)** |
| 2 | Implement `from_factors()` confidence scoring | S | High — currently a lie | This sprint | **DONE (pre-review)** |
| 3 | Add try/except + retry in pipeline nodes | M | High — production resilience | This sprint | **DONE** |
| 4 | Resolve HolmesGPT dep conflict or build `DirectToolsetAdapter` | L | Critical — core SRE value | Next sprint | **DONE (pre-review)** — `DirectToolsetAdapter` |
| 5 | Build intent router (thin classifier) | S | High — enables multi-agent | Next sprint | **DONE (pre-review)** |
| 6 | Implement Tier 2 eval framework (component evals) | M | High — catch regressions | Next sprint | **DONE** — `pydantic_evals` based |
| 7 | Add supervisor graph wrapping both pipelines | L | High — quality gating | Sprint +2 | **DONE** — orchestrator + quality gate |
| 8 | Human sign-off workflow (Slack approve/reject) | M | Critical for hedge fund compliance | Sprint +2 | **DONE** — approval gate + API |
| 9 | Trader-facing customer service agent | L | Medium — high visibility | Sprint +3 | Not started |
| 10 | Coding agent (runbook/monitor generation) | L | Medium — force multiplier | Sprint +3 | Not started |

---

## 7. Summary

Sentinel's foundation is solid — the layered architecture, type system, and vendor abstraction are production-grade. But you're at an inflection point: the system works as two isolated pipelines, and scaling to a true multi-agent platform requires the orchestration layer (supervisor, routing, approval gates) and the eval infrastructure to validate agent quality at each step. The biggest immediate risks are the synchronous webhook handlers (will break in prod), the stub confidence scoring (misleading metrics), and the HolmesGPT placeholder (core SRE pipeline returns nothing useful).

The eval framework design above gives you a path to catch quality regressions before they hit traders' desks, and the plugin architecture means every new agent you build comes with eval coverage by default.

> **Post-implementation note (2026-04-01):** Of the 10 priority recommendations, 8 are now resolved (6 were already done before this review, 2 more resolved in response). The remaining items (coding agent, customer service agent) are future-sprint work. The three "biggest immediate risks" identified in this paragraph — synchronous webhooks, stub confidence, HolmesGPT placeholder — were all resolved before the review was written. The review was based on a stale snapshot of the codebase.
