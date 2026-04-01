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
2. **Custom Kubernetes operators** — Integration with kagent/AgentGateway is deferred to a future phase pending evaluation
3. **Scheduled maintenance agents** — Toby's vision of recurring agentic jobs (e.g. repo maintenance every Thursday at 5pm) is noted as a future capability, not MVP
4. **Multi-tenant / multi-team** — V1 targets a single team's alerts and tickets
5. **Fine-tuned models** — V1 uses general-purpose LLMs via LiteLLM; fine-tuning is a future optimisation

## Requirements

Grouped into the following areas of focus:

1. AI SRE — Alert Investigation Pipeline
2. AI Support Agent — Ticket Review Pipeline
3. Infrastructure & Scalability
4. Observability & Feedback Loop

### 1. AI SRE — Alert Investigation Pipeline

As an **on-call engineer**, I want Sentinel to **automatically investigate production alerts and post a root cause summary to Slack and PagerDuty**, so that I can **understand the issue faster and begin remediation immediately**.

Acceptance criteria:

- [x] PagerDuty webhook ingests `incident.triggered` and `incident.escalated` events
- [x] Alert classifier determines severity, affected service, category, and urgency
- [x] Observability integration queries logs, metrics, and traces — implemented via `DirectToolsetAdapter` with pluggable backends: `DatadogClient` (production) or `GrafanaClient` querying Prometheus/Loki/Tempo (local dev, open-source alternative)
- [x] Root cause analyser synthesises findings into a structured summary with evidence, timeline, and remediation steps
- [x] Confidence score (low/medium/high) is calculated and displayed
- [x] Results are posted to a configurable Slack channel with formatted blocks
- [x] Results are added as a note on the PagerDuty incident
- [ ] Investigation completes within 2 minutes of alert receipt — not yet benchmarked in production

### 2. AI Support Agent — Ticket Review Pipeline

As a **support engineer**, I want Sentinel to **automatically review new Jira tickets, search documentation, and draft a response suggestion**, so that I can **respond to customers faster with accurate, source-backed answers**.

Acceptance criteria:

- [x] Jira webhook ingests `issue_created` and `issue_updated` events
- [x] Ticket classifier extracts category, urgency, key questions, and search queries
- [x] Documentation search runs in parallel across Confluence and similar Jira tickets
- [x] Response drafter produces a professional response with source attribution and confidence score
- [x] Results are posted to a configurable Slack channel
- [x] Feedback API allows accepting/rejecting/modifying suggestions (`POST /api/support/reviews/{id}/feedback`)
- [ ] Review completes within 3 minutes of ticket creation — not yet benchmarked in production

### 3. Infrastructure & Scalability

As a **platform engineer**, I want Sentinel to **run reliably at scale with proper job queuing, worker isolation, and graceful shutdown**, so that I can **trust it in production without babysitting**.

Acceptance criteria:

- [x] PostgreSQL-backed job queue with `SELECT ... FOR UPDATE SKIP LOCKED` for safe multi-replica processing
- [x] Background worker with configurable poll interval, job timeout (300s default), and max retries (3)
- [x] Stale job recovery for workers that crash mid-investigation
- [x] Helm chart with separate API and Worker deployments, HPA, PDB, and network policies
- [x] Graceful shutdown on SIGTERM/SIGINT with 330s termination grace period
- [x] Docker image based on Python 3.13-slim with non-root user
- [x] Alembic database migrations run as a Helm pre-upgrade job

### 4. Observability & Feedback Loop

As a **platform engineer**, I want **structured logging, distributed tracing, and error tracking**, so that I can **monitor Sentinel's health and improve its accuracy over time**.

Acceptance criteria:

- [x] Structured logging via structlog with context-aware event names (e.g. `alert_classified`, `investigation_completed`)
- [ ] Datadog APM integration for distributed tracing across the pipeline
- [x] Sentry integration for exception tracking
- [x] Audit trail of all investigations and reviews persisted to the database
- [x] Confidence score trends are trackable to measure accuracy improvement over time — multi-factor scoring (`from_factors`) with source count, relevance, and recency weights; feedback stats endpoint (`GET /api/support/stats`) tracks acceptance rates

## High complexity features

- **Pluggable observability backends** — `BaseObservabilityClient` ABC with `DatadogClient` and `GrafanaClient` implementations. Auto-selects Grafana for local dev, Datadog for production. Each backend provides its own query templates (Datadog query syntax vs PromQL/LogQL/TraceQL)
- **LiteLLM gateway routing** — All LLM calls route through a LiteLLM proxy, mapping model names to backend providers (Ollama for local dev, cloud providers for production). Configuration management across environments is non-trivial
- **Multi-source documentation search** — Parallel search across Confluence, Jira, and potentially S3/Notion requires careful timeout handling and result ranking
- **Job queue consistency** — Ensuring exactly-once processing with idempotency keys across multiple worker replicas under failure conditions
- **Kubernetes-native agent management** — Future evaluation of kagent and AgentGateway for deploying, scaling, and observing agents as first-class Kubernetes resources

## Technical Stuff

**Stack:** Python 3.13, FastAPI, PydanticAI, Pydantic Graph, PostgreSQL, SQLModel, LiteLLM, structlog, Sentry. Observability via Datadog (production) or Grafana + Prometheus + Loki + Tempo (local/open-source)

**Architecture:** Clean layered architecture enforced by import-linter:

- `interfaces/` → API routers, Pydantic Graph pipelines, webhook handlers, Slack bot
- `application/` → Use cases, job orchestration, persistence
- `domain/` → Business entities, search abstractions, vendor adapters
- `data/` → SQLModel models, Alembic migrations
- `vendors/` → External SDK wrappers

**Pipelines are Pydantic Graph DAGs:**

- SRE: ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → PublishFindings
- Support: ClassifyTicket → SearchDocumentation → DraftResponse → DetermineConfidence

**Serving suggestion:** Kubernetes via Helm chart with separate API deployment (user-facing, 2 replicas) and Worker deployment (background processing, 2 replicas). PostgreSQL for state. LiteLLM sidecar or shared gateway for LLM routing.

**Open questions from Toby:**

1. Where should scheduled agentic jobs live? Custom CronJob with Python + Agents SDK, or an OSS framework like kagent?
2. How do we build a feedback loop so investigations "keep getting better over time"?
3. Should we adopt AgentGateway for standardised agent-to-tool communication?
