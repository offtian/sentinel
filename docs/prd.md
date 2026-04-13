# PRD: Sentinel — AI SRE & Support Agent

> Notion source: https://www.notion.so/33123918d44d8178880ce9a14a65b013

## Summary

### Background

Our platform engineering team manages a growing number of production services across Kubernetes. Alert fatigue from PagerDuty and repetitive Jira Service Desk tickets consume significant on-call and support engineering time. Meanwhile, the industry is rapidly adopting agentic AI for operational tasks — tools like HolmesGPT, kagent, and AgentGateway demonstrate the viability of AI-driven SRE and support workflows.

Toby flagged this opportunity after attending KubeCon talks on agentic management for Kubernetes, noting the gap between working POCs and production-grade, scalable agent systems.

### Opportunity

**Problem:** On-call engineers spend excessive time triaging alerts and investigating root causes manually, while support engineers repeatedly search the same documentation to answer similar tickets. This leads to alert fatigue, slow incident response, and inconsistent support quality.

### Objective

Build Sentinel — an AI-powered automation platform that automatically triages production alerts (AI SRE) and reviews support tickets (AI Support Agent), delivering investigation results and response suggestions back to engineers via Slack, PagerDuty, and Jira.

## Project overview

### Impact

Reduce mean time to investigate (MTTI) for production alerts and mean time to first response (MTTR) for support tickets. Free up engineering time from repetitive triage work so teams can focus on remediation and higher-value support.

### Success criteria

- **MTTI reduction** — Automated alert investigation completes within 2 minutes of alert firing; target 60% reduction in manual investigation time
- **Support first-response time** — AI-drafted responses available within 3 minutes of ticket creation; target 50% reduction in first-response time
- **Accuracy** — High-confidence investigations (>80% confidence score) are accurate in 90%+ of cases
- **Adoption** — On-call engineers find Sentinel suggestions useful in 70%+ of alerts within the first quarter
- **Coverage** — AI SRE handles 80%+ of common alert categories; AI Support handles 70%+ of recurring ticket types

### Assumptions

1. **Feasibility** — LLMs (routed via LiteLLM) can reliably classify alerts and tickets with sufficient accuracy when given structured context from observability tools and documentation
2. **Feasibility** — HolmesGPT can query Datadog logs/metrics, Kubernetes state, and traces to produce useful investigation context
3. **Viability** — The time saved by automated triage justifies the infrastructure and LLM compute costs
4. **Desirability** — Engineers want AI-assisted investigation summaries alongside alerts, not replacing their judgment but accelerating their workflow
5. **Usability** — Posting results to Slack and PagerDuty (where engineers already work) removes friction vs. requiring a separate UI
6. **Ethical** — Sentinel provides suggestions only; humans make all remediation and customer-facing decisions

### Out of scope

1. **Automated remediation** — Sentinel investigates and suggests, but does not execute fixes or auto-respond to customers
2. ~~**Custom Kubernetes operators**~~ — kagent integration delivered (CRD-based delegation with polling, comparison mode). AgentGateway deferred until multiple MCP backends justify it
3. **Multi-tenant / multi-team** — V1 targets a single team's alerts and tickets
4. **Fine-tuned models** — V1 uses general-purpose LLMs via LiteLLM; fine-tuning is a future optimisation

---

## Requirements

Grouped into the following areas of focus:

1. AI SRE — Alert Investigation Pipeline
2. AI Support Agent — Ticket Review Pipeline
3. Infrastructure & Scalability
4. Observability & Feedback Loop
5. Scheduled Automations
6. Hedge Fund Compliance & Quality Gating
7. Agent Capability Platform — Skills, MCP & Cutting-Edge Capabilities

### 1. AI SRE — Alert Investigation Pipeline

As an **on-call engineer**, I want Sentinel to **automatically investigate production alerts and post a root cause summary to Slack and PagerDuty**, so that I can **understand the issue faster and begin remediation immediately**.

Acceptance criteria:

- [x] PagerDuty webhook ingests `incident.triggered` and `incident.escalated` events
- [x] Alert classifier determines severity, affected service, category, and urgency
- [x] Observability integration queries logs, metrics, and traces — implemented via `DirectToolsetAdapter` with pluggable backends: `DatadogClient` (production) or `GrafanaClient` querying Prometheus/Loki/Tempo (local dev, open-source alternative)
- [x] Root cause analyser synthesises findings into a structured summary with evidence, timeline, and remediation steps
- [x] Confidence score (low/medium/high) is calculated via multi-factor scoring (`ConfidenceScore.from_factors`) weighing source count (30%), relevance (50%), and recency (20%)
- [x] Results are posted to a configurable Slack channel with formatted blocks
- [x] Results are added as a note on the PagerDuty incident
- [ ] Investigation completes within 2 minutes of alert receipt — not yet benchmarked in production
- [x] SRE agents auto-load MCP toolsets discovered from `MCP_SERVERS` — shared via `Configuration.build_mcp_toolsets()`, injected as `classifier_toolsets` and `analyser_toolsets`
- [x] Alert classifier output drives runbook **skill** selection (e.g. `k8s-crashloop-runbook`) appended to root-cause-analyser context — `ClassifyAlert` stores `classification_category`, passed to `root_cause_analyser.Dependencies(category=...)`, which calls `_inject_runbook_skills()` → `render_skills_section(category=...)`
- [x] Prompt caching enabled on all SRE agent system prompts via PydanticAI `model_settings` (Anthropic `cache_instructions` / OpenAI `prompt_cache_key`)

### 2. AI Support Agent — Ticket Review Pipeline

As a **support engineer**, I want Sentinel to **automatically review new Jira tickets, search documentation, and draft a response suggestion**, so that I can **respond to customers faster with accurate, source-backed answers**.

Acceptance criteria:

- [x] Jira webhook ingests `issue_created` and `issue_updated` events
- [x] Ticket classifier extracts category, urgency, key questions, and search queries
- [x] Documentation search runs in parallel across Confluence and similar Jira tickets — keyword-only; hybrid search (BM25 + embeddings) needed for production-grade semantic matching
- [x] Response drafter produces a professional response with source attribution and confidence score
- [x] Results are posted to a configurable Slack channel
- [x] Feedback API allows accepting/rejecting/modifying suggestions (`POST /api/support/reviews/{id}/feedback`)
- [x] Feedback stats endpoint tracks acceptance rates over time (`GET /api/support/stats`)
- [ ] Review completes within 3 minutes of ticket creation — not yet benchmarked in production
- [x] Support agents auto-load MCP toolsets discovered from `MCP_SERVERS` — shared via `Configuration.build_mcp_toolsets()`, injected as `reviewer_toolsets` and `drafter_toolsets`
- [ ] Ticket classifier output drives response **skill** selection (e.g. `auth-error-response`, `rate-limit-response`)
- [x] Prompt caching enabled on ticket reviewer + response drafter system prompts

### 3. Infrastructure & Scalability

As a **platform engineer**, I want Sentinel to **run reliably at scale with proper job queuing, worker isolation, and graceful shutdown**, so that I can **trust it in production without babysitting**.

Acceptance criteria:

- [x] PostgreSQL-backed job queue with `SELECT ... FOR UPDATE SKIP LOCKED` for safe multi-replica processing
- [x] Background worker with configurable poll interval, job timeout (300s default), and max retries (3)
- [x] Worker `--run-once` mode for Kubernetes CronJob execution (claims one job, executes, exits)
- [x] Three job types: `SRE_INVESTIGATION`, `SUPPORT_REVIEW`, `SCHEDULED_AUTOMATION`
- [x] Stale job recovery for workers that crash mid-investigation
- [x] Helm chart with separate API and Worker deployments, HPA, PDB, and network policies
- [x] Graceful shutdown on SIGTERM/SIGINT with 330s termination grace period
- [x] Docker image based on Python 3.13-slim with non-root user
- [x] Alembic database migrations run as a Helm pre-upgrade job
- [x] CI/CD pipeline (`.github/workflows/ci.yml`) with lint, unit tests, integration tests (PostgreSQL), and Docker build

### 4. Observability & Feedback Loop

As a **platform engineer**, I want **structured logging, distributed tracing, and error tracking**, so that I can **monitor Sentinel's health and improve its accuracy over time**.

Acceptance criteria:

- [x] Structured logging via structlog with context-aware event names (e.g. `alert_classified`, `investigation_completed`)
- [x] PydanticAI spans (`Agent(..., instrument=True)`) exported via OTLP — Logfire SDK configured in `bootstrap_otel.init_traces()`, exports to Tempo/Datadog via `otel_traces_endpoint`
- [x] Per-pipeline-run snapshot persisted to `pipeline_runs` / `node_executions` / `agent_calls` (graph nodes write to the existing tracing tables)
- [x] Token usage and cost recorded per agent call — `record_agent_result()` extracts `result.usage()` from PydanticAI runs in all SRE and support graph nodes; `estimate_cost_usd()` computes USD cost via LiteLLM pricing lookup; `token_cost_usd` field on `EvaluationMetrics` for backend comparison
- [ ] Skill activations logged as structlog events and persisted to the audit log
- [x] Sentry integration for exception tracking
- [x] Audit trail of all investigations and reviews persisted to the database
- [x] Multi-factor confidence scoring (`ConfidenceScore.from_factors`) with source count, relevance, and recency weights
- [x] Feedback stats endpoint (`GET /api/support/stats`) returns acceptance/rejection rates for tracking accuracy improvement
- [x] Evaluation framework with golden test datasets (5 SRE + 5 support cases) and automated quality rubrics (`just test-evals`)
- [x] Custom OTel metrics instruments declared (`utils/metrics.py`): investigation/review counters, pipeline node duration histogram, confidence histogram, job queue metrics
- [ ] LLM call metrics (`sentinel_llm_calls_total`, `sentinel_llm_call_duration_seconds`) declared but `record_llm_call()` never invoked from pipeline nodes
- [ ] Approval decision metrics (`sentinel_approval_decisions_total`) declared but `record_approval_decision()` never invoked
- [ ] SRE approval persistence — currently in-memory dict (`_pending_approvals`), data lost on restart; needs database-backed store
- [ ] SRE investigation stats endpoint (parity with `GET /api/support/stats`) — approval rates, investigation outcomes over time
- [ ] Quality verdict persistence — `QualityVerdict` (score + issues) computed by quality gate but not stored on investigation/review records

### 5. Scheduled Automations

As a **platform engineer**, I want to **run recurring agentic tasks on a schedule**, so that I can **automate operational workflows like repository health checks without a separate runtime**.

Acceptance criteria:

- [x] `SCHEDULED_AUTOMATION` job type in the job queue
- [x] Automation registry pattern (`application/automations/runner.py`) with named automations
- [x] `POST /api/automations/trigger` to manually trigger an automation
- [x] `GET /api/automations/available` to list registered automations
- [x] First automation: `repo_health_check` (placeholder — needs GitHub API integration)
- [x] Worker `--run-once` mode enables CronJob-based scheduling
- [x] MCP tool integration (FastMCP) for external tool communication — FastMCP server exposing observability, documentation, and investigation tools; MCP client builder for consuming external MCP servers via PydanticAI

### 6. Hedge Fund Compliance & Quality Gating

As a **risk officer**, I want **human approval gates and quality checks on all automated outputs**, so that **no investigation finding or response suggestion reaches external systems without oversight**.

Acceptance criteria:

- [x] Graceful error handling in all pipeline nodes — critical nodes fail cleanly, degradable nodes continue with partial results
- [x] `PublishFindings` uses `gather(return_exceptions=True)` so one failed channel does not block others
- [x] Human approval gate: low-confidence investigations require Slack approve/reject before publishing to PagerDuty
- [x] Approval API endpoints: `POST /approve`, `POST /reject`, `GET /approval-status` on investigations
- [x] Configurable confidence threshold (`require_approval_below_confidence`, default 0.7)
- [x] Immutable `ApprovalRequest` domain entity with approve/reject/auto-approve transitions
- [x] Append-only audit log with SHA-256 input hashes for regulatory traceability
- [x] Supervisor graph wrapping both pipelines with rule-based quality gate before publishing
- [x] Tier 2 component evaluations: per-agent quality scoring with golden datasets
- [x] Every prompt template carries a `prompt_version` (git SHA + filename) and a `prompt_sha256` hash, recorded on each `AgentCallRecord` and `AuditLogRecord`
- [ ] Skill files are content-hashed and the active hash is recorded alongside the prompt hash
- [x] `replay_pipeline_run(run_id)` re-executes a historical run from its snapshot — prompt version, model id, MCP servers, skills, and input payload — for regulator playback — implemented via `python -m sentinel.replay <run_id> --replay` with `--diff` for output comparison

### 7. Agent Capability Platform — Skills, MCP & Cutting-Edge Capabilities

As a **platform engineer**, I want **Sentinel agents to share a uniform capability plane (Skills + MCP + prompt caching + telemetry)**, so that **adding a new runbook, tool server, or model is a config change rather than a code change**.

Acceptance criteria:

- [x] `src/sentinel/domain/skills/<name>/SKILL.md` directory layout with frontmatter (`name`, `description`, `applies_to`, `version`)
- [x] `domain.skills.load_skills_for(category=..., max=N)` helper returns matching skills, sorted deterministically
- [x] Skills are appended to the system prompt by `interfaces/graphs/agents/utils.py` so every agent picks them up uniformly
- [x] `Configuration.build_mcp_toolsets()` is the single place that builds the shared MCP toolset list, consumed by all SRE and Support pipeline dependencies — thread-safe with double-checked locking, memoised per instance
- [x] `MCP_SERVERS` documented in `.env.default` with examples for Datadog MCP, GitHub MCP, Confluence MCP
- [x] `bootstrap.initialise()` configures an OTLP exporter via Logfire SDK (`bootstrap_otel.init_traces()`) — `send_to_logfire=False` routes spans to Tempo/Datadog via `otel_traces_endpoint`
- [x] FastMCP server (`interfaces/mcp/server.py`) gains a `list_skills` tool exposing the installed skill catalogue to external agents

---

## Completion Status

### Pipeline Completion Estimates

- **AI SRE Pipeline** — ~98% complete. Core pipeline functional end-to-end. HolmesGPT SDK integrated via fork; DirectToolsetAdapter is primary with HolmesAdapter as opt-in alternative. Prompt caching, prompt versioning, and replay re-execution all shipped. Key gaps: production benchmarking against real incidents.
- **AI Support Pipeline** — ~85% complete. Core pipeline functional end-to-end. Prompt caching and versioning shipped. Key gaps: hybrid documentation search (keyword-only today), ticket classifier skill selection, production benchmarking.

### Resolved — Originally Out of Scope

The following items were originally listed as out of scope but have since been delivered:

| Item | Resolution | Delivered In |
|------|------------|--------------|
| Scheduled maintenance agents | `SCHEDULED_AUTOMATION` job type, automation registry, `--run-once` worker mode, `POST /api/automations/trigger` API, Helm CronJob template | Phase C |

### Toby's Questions

These were raised during the initial project kickoff after KubeCon. Each has been investigated and resolved (or deferred with reasoning).

#### Q1: Where should scheduled agentic jobs live? Custom CronJob with Python + Agents SDK, or an OSS framework like kagent?

**Answer: Sentinel's own worker with `--run-once` mode, triggered by Kubernetes CronJobs.**

We chose the simplest architecture that avoids introducing a new runtime. The worker already exists for async pipeline execution — adding `--run-once` mode (`worker.py:237-275`) lets a CronJob spin up a worker pod that claims exactly one job, runs it, and exits. This is the same pattern used by Sidekiq and Celery beat in the Rails/Django ecosystem: the scheduler (K8s CronJob) is decoupled from the executor (Sentinel worker).

Why not kagent or Temporal:
- **kagent** is a Kubernetes operator that manages agent CRDs. It's well-suited when you need K8s-native lifecycle management (auto-scaling agents, health probes per agent). But Sentinel's automations are short-lived batch jobs (health checks, repo scans), not long-running agents — a CronJob is the idiomatic K8s primitive for this.
- **Temporal** adds a separate cluster dependency (Temporal server + database). For "run this Python function on a schedule", that's unnecessary infrastructure. The PostgreSQL job queue (`application/jobs/`) already handles retries, timeouts, and stale job recovery.
- **Argo Workflows** was considered but rejected — it orchestrates DAGs of containers, whereas our automations are single-step Python functions. Overkill.

The escape hatch: the automation registry pattern (`application/automations/runner.py`) uses a `register_automation()` decorator, so if a future automation needs multi-step orchestration, it can internally call Temporal or spawn sub-jobs without changing the scheduling layer.

**Implementation:** `worker.py` `--run-once` flag, `application/automations/runner.py` registry, `POST /api/automations/trigger` endpoint, Helm CronJob template in `helm/sentinel/values.yaml`.

#### Q2: How do we build a feedback loop so investigations "keep getting better over time"?

**Answer: Three-layer feedback loop — human feedback API, multi-factor confidence scoring, and golden-case evaluation framework.**

This mirrors the standard RLHF-lite pattern used in production agent systems (e.g., Notion AI, Intercom's Fin, Klarna's support agent):

1. **Human feedback collection** — Support engineers accept, reject, or modify AI-drafted responses via `POST /api/support/reviews/{id}/feedback`. Each decision is timestamped and stored with the original suggestion. The `GET /api/support/stats` endpoint aggregates acceptance rates, giving a live accuracy signal without requiring labelled datasets. This is the same pattern Intercom uses with their "Fin" AI agent — every resolved conversation becomes a training signal.

2. **Multi-factor confidence scoring** — `ConfidenceScore.from_factors()` (`domain/confidence/entities.py:49-99`) independently scores three dimensions: source evidence count (30% weight), relevance quality (50%), and data recency (20%). This decomposition means you can track *why* confidence is low (no sources? stale data? poor relevance?) rather than just a single opaque number. The label thresholds (HIGH >= 0.7, MEDIUM >= 0.4) directly gate the approval workflow — investigations below the threshold require human sign-off before publishing.

3. **Evaluation framework** — Golden test datasets (`tests/evals/datasets/`) with known-good alert→investigation pairs. Each case specifies expected keywords, minimum confidence thresholds, and expected labels. Running `just test-evals` validates that prompt changes or model swaps don't regress quality. This is the "eval-driven development" pattern advocated by Anthropic and OpenAI for production agent systems — you write the eval before changing the prompt, same as TDD for code.

The feedback data feeds back into the eval loop: when a human rejects an investigation, the case can be added to the golden dataset as a regression test. Over time, the golden dataset grows to reflect real failure modes seen in production.

**Implementation:** `POST /api/support/reviews/{id}/feedback`, `GET /api/support/stats` (`interfaces/api/routers/support/router.py`), `ConfidenceScore.from_factors()` (`domain/confidence/entities.py`), `tests/evals/` framework.

#### Q3: Should we adopt AgentGateway for standardised agent-to-tool communication?

**Answer: Deferred. Adopt MCP (Model Context Protocol) via FastMCP first; evaluate AgentGateway when we have multiple agent runtimes.**

AgentGateway is a proxy that sits between agents and tools, providing centralised routing, auth, and observability. It solves a real problem — but only when you have **multiple agent runtimes** calling the same tools (e.g., a PydanticAI agent and a LangGraph agent both needing Datadog access). Sentinel currently has one runtime (PydanticAI + Pydantic Graph), so AgentGateway would be an extra network hop with no practical benefit.

The better near-term investment is **MCP (Model Context Protocol)**. MCP standardises how agents discover and call tools without requiring a centralised gateway. PydanticAI has native MCP client support, and FastMCP provides a lightweight server implementation. This gives us:
- Tool discoverability (agents learn what tools are available at runtime)
- Schema-driven tool calling (no custom adapter code per tool)
- Portability (MCP tools work across agent frameworks)

AgentGateway becomes relevant when:
- We add a second agent runtime (kagent for K8s, Claude Agent SDK for coding tasks)
- We need centralised tool auth (one API key per tool, not per agent)
- We need cross-agent observability (which agent called which tool, when)

**Status:** MCP integration shipped — FastMCP server at `interfaces/mcp/`, MCP client builder at `plugins/toolsets/mcp.py`, universal injection via `Configuration.build_mcp_toolsets()`. AgentGateway evaluation deferred until a second agent runtime is added.

### Resolved Dependencies

**HolmesGPT SDK** — installed via fork (`offtian/holmesgpt@httpx-compat`) which relaxes httpx and postgrest pins. DirectToolsetAdapter remains the primary/default investigation engine. `HolmesAdapter` provides a real SDK integration as an opt-in alternative.

### Remaining Gaps

| Gap | Blocked By | Target |
|-----|------------|--------|
| **Online Metrics** | | |
| SRE approval persistence | Section 4 — `_pending_approvals` in-memory dict loses data on restart; needs `approval_records` table + Alembic migration | Phase E |
| SRE investigation stats endpoint | Section 4 — no `/api/sre/stats` parity with support; cannot track approval rates or investigation outcomes | Phase E |
| Quality verdict persistence | Section 4 — `QualityVerdict` computed but not stored on investigation/review records | Phase E |
| **Offline Metrics** | | |
| ~~Token usage extraction from agent results~~ | ~~Delivered in PR #18~~ | ~~Done~~ |
| ~~LLM cost estimation~~ | ~~Delivered in PR #18 — `estimate_cost_usd()` with LiteLLM pricing lookup~~ | ~~Done~~ |
| LLM call OTel metrics wiring | Section 4 — `sentinel_llm_calls_total` and `sentinel_llm_call_duration_seconds` declared but `record_llm_call()` never invoked | Phase E |
| Approval decision OTel metrics | Section 4 — `sentinel_approval_decisions_total` declared but `record_approval_decision()` never invoked | Phase E |
| **Pipeline Improvements** | | |
| Hybrid documentation search (BM25 + embeddings) | Section 2 — Confluence keyword-only search misses semantic matches | Phase E |
| Ticket classifier skill selection | Section 2 — `auth-error-response`, `rate-limit-response` skills exist but not triggered from ticket classifier output | Phase E |
| Skill content hash in audit log | Section 6 — `SkillHandle.sha256` computed but not persisted alongside prompt hash in `AuditLogRecord` | Phase E |
| **Infrastructure** | | |
| ~~LiteLLM deployment mode~~ | ~~Delivered in PR #17 — removed proxy, routes via LiteLLM SDK in-process~~ | ~~Done~~ |
| Eval framework maturity | pydantic_evals lacks dashboard, regression tracking, community metrics. Consider DeepEval or Braintrust | Phase E |
| **Post-Deploy Validation** | | |
| Investigation < 2min benchmark | Production deployment (separate repo) | Post-deploy |
| Review < 3min benchmark | Production deployment (separate repo) | Post-deploy |
| Real incident data validation | All development has been against synthetic data. Architecture is sound but unvalidated against real PagerDuty alerts, Jira tickets, and incident data | Post-deploy |

---

## High complexity features

- **Pluggable observability backends** — `BaseObservabilityClient` ABC with `DatadogClient` and `GrafanaClient` implementations. Auto-selects Grafana for local dev, Datadog for production. Each backend provides its own query templates (Datadog query syntax vs PromQL/LogQL/TraceQL)
- **LiteLLM SDK routing** — All LLM calls route through LiteLLM SDK in-process via PydanticAI's `litellm:` model prefix, mapping model names to backend providers (Ollama for local dev, cloud providers for production)
- **Multi-source documentation search** — Parallel search across Confluence, Jira, and potentially S3/Notion requires careful timeout handling and result ranking
- **Job queue consistency** — Ensuring exactly-once processing with idempotency keys across multiple worker replicas under failure conditions
- **Multi-factor confidence scoring** — `ConfidenceScore.from_factors()` independently weighs source count, relevance, and recency with configurable weights
- **Human approval gate** — Confidence-gated publishing with Slack interactive messages (approve/reject buttons) for hedge fund compliance
- **Kubernetes-native agent management** — Dual K8s investigation backends (native PydanticAI agent + kagent CRD delegation) with config-driven A/B comparison mode, read-only RBAC, and typed audit trail for hedge fund compliance
- **Skills + MCP capability plane** — runtime composition of agent capabilities from on-disk Skills (procedural runbooks) and remote MCP tool servers, with deterministic ordering, content hashing, and per-run capture for replay

## Technical Stuff

**Stack:** Python 3.13, FastAPI, PydanticAI, Pydantic Graph, PostgreSQL, SQLModel, LiteLLM, structlog, Sentry. Observability via Datadog (production) or Grafana + Prometheus + Loki + Tempo (local/open-source)

**Architecture:** Clean layered architecture enforced by import-linter:

- `interfaces/` → API routers, Pydantic Graph pipelines, webhook handlers, Slack bot
- `application/` → Use cases, job orchestration, persistence, automation runner
- `domain/` → Business entities, search abstractions, vendor adapters
- `data/` → SQLModel models, Alembic migrations
- `vendors/` → External SDK wrappers

**Pipelines are Pydantic Graph DAGs:**

- SRE: ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → [ApprovalGate] → PublishFindings
- Support: ClassifyTicket → SearchDocumentation → DraftResponse → DetermineConfidence

**API surface:**

- `POST /api/sre/webhooks/pagerduty` — PagerDuty webhook receiver
- `POST /api/sre/webhooks/datadog` — Datadog webhook receiver
- `POST /api/sre/investigate` — Manual investigation trigger
- `GET /api/sre/investigations/{id}` — Fetch investigation result
- `POST /api/sre/investigations/{id}/approve` — Approve investigation for publishing
- `POST /api/sre/investigations/{id}/reject` — Reject investigation
- `GET /api/sre/investigations/{id}/approval-status` — Check approval status
- `POST /api/support/webhooks/jira` — Jira webhook receiver
- `POST /api/support/review` — Manual review trigger
- `GET /api/support/reviews/{id}` — Fetch review result
- `POST /api/support/reviews/{id}/feedback` — Submit review feedback
- `GET /api/support/stats` — Feedback acceptance rates
- `POST /api/automations/trigger` — Trigger a scheduled automation
- `GET /api/automations/available` — List registered automations
- `GET /api/jobs/{id}` — Check job status

**Serving suggestion:** Kubernetes via Helm chart with separate API deployment (user-facing, 2 replicas) and Worker deployment (background processing, 2 replicas). PostgreSQL for state. LiteLLM sidecar or shared gateway for LLM routing.

**Test suite:** 770+ tests — unit, functional, evaluation, and integration. Golden test datasets with automated quality rubrics.
