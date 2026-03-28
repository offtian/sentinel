# Sentinel Roadmap

## Current Status: Phase 2 Complete (AI SRE Polish + Deployment Scaffolding)

### Phase 1: Bootstrap + AI SRE Core - COMPLETE

- Repository scaffolding (pyproject.toml, Makefile, Dockerfile, docker-compose.yml)
- Clean architecture skeleton with import-linter contracts (3 contracts: top-level layers, domain sub-layers, interfaces sub-layers)
- Centralised configuration (`_config.py`) with all environment variables
- Domain entities for SRE (Alert, Investigation, Finding, AlertSeverity)
- Domain entities for Support (Ticket, TicketComment, ResponseSuggestion, DocSource)
- Confidence scoring framework (ConfidenceScore, ConfidenceLabel)
- Search abstraction layer (BaseDocumentSearcher, BaseMetricsSearcher, BasePastTicketSearcher)
- HolmesGPT adapter with ABC, production adapter (placeholder), and mock adapter
- 4 PydanticAI agents: alert_classifier, root_cause_analyser, ticket_reviewer, response_drafter
- SRE investigation Pydantic Graph pipeline (5 nodes: Classify → Investigate → Analyse → Confidence → Publish)
- Support review Pydantic Graph pipeline (4 nodes: Classify → Search → Draft → Confidence)
- PagerDuty V3 and Datadog webhook parsers
- FastAPI app with SRE and Support routers
- Slack message formatting for investigations and support suggestions
- Database models (InvestigationRecord, TicketReviewRecord)
- CLAUDE.md for Claude Code integration
- 29 unit tests passing

---

## Phase 2: AI SRE Polish + Deployment - COMPLETE

### Implemented

1. **Datadog Vendor Adapter** (`domain/vendor_adapters/datadog_client.py`)
   - Wraps `datadog-api-client` SDK for logs, metrics, and monitor queries
   - Graceful no-op when not configured (`is_configured` property)
   - Deferred SDK imports to avoid import-time side effects

2. **PagerDuty Vendor Adapter** (`domain/vendor_adapters/pagerduty.py`)
   - Wraps `pdpyras` for incident notes, details, and status updates
   - `format_investigation_note()` generates markdown for PagerDuty notes
   - Graceful no-op when not configured

3. **Jira Vendor Adapter** (`domain/vendor_adapters/jira.py`)
   - Wraps `jira` SDK for issue CRUD, JQL search, internal comments, transitions
   - `format_suggestion_comment()` generates Jira wiki markup for response suggestions
   - Internal comments use Service Desk Team role visibility

4. **Confluence Vendor Adapter** (`domain/vendor_adapters/confluence.py`)
   - Wraps `atlassian-python-api` for CQL search and page content retrieval
   - `_html_to_plain_text()` converts Confluence storage format HTML to plain text

5. **Database Persistence Layer**
   - `data/database.py` - Async engine/session factory management with lazy-initialised singletons
   - `application/sre/persist.py` - Save and query investigation records
   - `application/support/persist.py` - Save and query ticket review records
   - FastAPI lifespan manages DB connection lifecycle (init on startup, close on shutdown)

6. **Pipeline Wiring**
   - `PublishFindings` node now posts to Slack, adds PagerDuty incident notes, and persists to database
   - Persistence injected as `PersistInvestigationFn` callback via `Dependencies` dataclass
   - PagerDuty write-back via `PagerDutyClient.add_incident_note()`

7. **Helm Chart** (`helm/sentinel/`)
   - Multi-deployment pattern (api + worker from same image)
   - Pre-install/upgrade migration job (alembic)
   - 9 template files + values.yaml

8. **CI/CD Pipeline** (`.circleci/config.yml`)
   - 4-job pipeline: mypy → test-and-lint → publish-image → package-chart
   - PostgreSQL sidecar for integration tests
   - Uses deployment orb for chart packaging

9. **Unit Tests** - 68 total (39 new)
   - Vendor adapter tests: Datadog (7), PagerDuty (7), Jira (10), Confluence (11)
   - Database session management tests (3)
   - API app lifecycle test (1)

10. **Code Quality** - All passing
    - ruff check + format
    - mypy strict mode
    - import-linter (3 contracts)

### Not Yet Implemented (Phase 2 gaps)

1. **HolmesGPT SDK Integration** — Still a placeholder adapter due to upstream pydantic-ai dependency conflict. The `BaseHolmesAdapter` ABC and `MockHolmesAdapter` are in place; `HolmesGPTAdapter` needs the actual SDK wired in once the conflict is resolved.

2. **Custom Toolsets** — Alternative to HolmesGPT: implement `DatadogToolset`, `KubernetesToolset`, `PrometheusToolset` as `BaseToolset` implementations that the adapter orchestrates. These would use the vendor adapters we've already built.

3. **Integration Tests** — Only unit tests written so far. End-to-end tests (webhook → pipeline → database) with mock LLM responses (PydanticAI test mode) are needed.

4. **Infrastructure Setup** — ECR repositories, IAM role, KMS key, ACM certificate, and `ktl-services-deployment` application directory need to be created in OctoCloud/ktl-services-deployment repos.

---

## Phase 3: AI Support Agent Core - NOT STARTED

### Implemented

1. **Document Search Implementations**
   - `NotionSearcher` implementing `BaseDocumentSearcher` — query via Bedrock KB or S3
   - `ConfluenceSearcher` implementing `BaseDocumentSearcher` — query via the Confluence vendor adapter
   - `S3DocumentSearcher` implementing `BaseDocumentSearcher` — direct S3 retrieval
   - `JiraPastTicketSearcher` implementing `BasePastTicketSearcher` — JQL for resolved tickets via the Jira vendor adapter

2. **PagerDuty Vendor Adapter** (`domain/vendor_adapters/pagerduty.py`)
   - Wraps `pdpyras` for incident notes, details, and status updates
   - `format_investigation_note()` generates markdown for PagerDuty notes
   - Graceful no-op when not configured

3. **Jira Vendor Adapter** (`domain/vendor_adapters/jira.py`)
   - Wraps `jira` SDK for issue CRUD, JQL search, internal comments, transitions
   - `format_suggestion_comment()` generates Jira wiki markup for response suggestions
   - Internal comments use Service Desk Team role visibility

4. **Confluence Vendor Adapter** (`domain/vendor_adapters/confluence.py`)
   - Wraps `atlassian-python-api` for CQL search and page content retrieval
   - `_html_to_plain_text()` converts Confluence storage format HTML to plain text

5. **Database Persistence Layer**
   - `data/database.py` - Async engine/session factory management with lazy-initialised singletons
   - `application/sre/persist.py` - Save and query investigation records
   - `application/support/persist.py` - Save and query ticket review records
   - FastAPI lifespan manages DB connection lifecycle (init on startup, close on shutdown)

6. **Pipeline Wiring**
   - `PublishFindings` node now posts to Slack, adds PagerDuty incident notes, and persists to database
   - Persistence injected as `PersistInvestigationFn` callback via `Dependencies` dataclass
   - PagerDuty write-back via `PagerDutyClient.add_incident_note()`

7. **Helm Chart** (`helm/sentinel/`)
   - Multi-deployment pattern (api + worker from same image)
   - Pre-install/upgrade migration job (alembic)
   - 9 template files + values.yaml

8. **CI/CD Pipeline** (`.circleci/config.yml`)
   - 4-job pipeline: mypy → test-and-lint → publish-image → package-chart
   - PostgreSQL sidecar for integration tests
   - Uses `krakentech/ktl-services-deployment-orb`

9. **Unit Tests** - 68 total (39 new)
   - Vendor adapter tests: Datadog (7), PagerDuty (7), Jira (10), Confluence (11)
   - Database session management tests (3)
   - API app lifecycle test (1)

10. **Code Quality** - All passing
    - ruff check + format
    - mypy strict mode
    - import-linter (3 contracts)

### Not Yet Implemented (Phase 2 gaps)

1. **HolmesGPT SDK Integration** — Still a placeholder adapter due to upstream pydantic-ai dependency conflict. The `BaseHolmesAdapter` ABC and `MockHolmesAdapter` are in place; `HolmesGPTAdapter` needs the actual SDK wired in once the conflict is resolved.

2. **Custom Toolsets** — Alternative to HolmesGPT: implement `DatadogToolset`, `KubernetesToolset`, `PrometheusToolset` as `BaseToolset` implementations that the adapter orchestrates. These would use the vendor adapters we've already built.

3. **Integration Tests** — Only unit tests written so far. End-to-end tests (webhook → pipeline → database) with mock LLM responses (PydanticAI test mode) are needed.

4. **Infrastructure Setup** — ECR repositories, IAM role, KMS key, ACM certificate, and `ktl-services-deployment` application directory need to be created in OctoCloud/ktl-services-deployment repos.

---

## Phase 3: AI Support Agent Core - NOT STARTED

### Must Implement

1. **Document Search Implementations**
   - `NotionSearcher` implementing `BaseDocumentSearcher` — query via Bedrock KB or S3 (reuse alfredo's pattern)
   - `ConfluenceSearcher` implementing `BaseDocumentSearcher` — query via the Confluence vendor adapter
   - `S3DocumentSearcher` implementing `BaseDocumentSearcher` — direct S3 retrieval
   - `JiraPastTicketSearcher` implementing `BasePastTicketSearcher` — JQL for resolved tickets via the Jira vendor adapter

2. **Jira Webhook Handler** (`interfaces/webhooks/jira.py`)
   - Separate module from the router for clean parsing (matching PagerDuty/Datadog pattern)
   - Parse Jira webhook payload into Ticket entity
   - Handle issue_created and issue_updated events
   - Filter by project/issue type to avoid processing irrelevant tickets

3. **Wire Up Search in Pipelines**
   - Connect document and ticket searchers to the `SearchDocumentation` graph node
   - Configure which searchers are active via feature flags
   - Parallel search execution with error isolation (one failing searcher doesn't break the flow)

4. **Support Persistence Wiring**
   - Wire `PersistTicketReviewFn` into the support pipeline (same pattern as SRE)
   - Add Jira write-back (post internal comment with suggestion) to pipeline output

5. **Unit Tests**
   - Jira webhook parsing tests
   - Search implementation tests (with mocked backends)
   - Support pipeline integration test with mock agents

**Note**: Jira and Confluence vendor adapters are already built (Phase 2). This phase focuses on the search layer and pipeline wiring.

---

## Phase 4: Polish + E2E - NOT STARTED

### Must Implement

1. **Feedback Loop for Support Suggestions**
   - Track whether suggestions are accepted, rejected, or modified
   - Store feedback in TicketReviewRecord (status field)
   - API endpoint: `POST /api/support/feedback`
   - Use feedback data to improve prompt engineering over time

2. **Past Ticket Search** (`JiraPastTicketSearcher`)
   - JQL query for resolved tickets with similar text
   - Extract resolution summaries
   - Rank by recency and relevance

3. **Evaluation Framework** (`tests/evals/`)
   - Adapt evaluation patterns for both pipelines
   - Metrics: accuracy of classification, quality of root cause analysis, quality of response drafts
   - Run against golden test cases

4. **Confidence Scoring Improvements**
   - SRE: Factor in number of data sources queried, data freshness, correlation strength
   - Support: Factor in source count, source authority (docs vs Slack), recency, grounding score

5. **Production Deployment**
   - Deploy to test cluster
   - Configure PagerDuty webhook subscription
   - Configure Jira webhook subscription
   - Set up Datadog monitors for Sentinel itself
   - Production deployment with CD enabled

6. **Operational Runbook** (`docs/runbook.md`)
   - How to deploy, scale, troubleshoot
   - How to add new alert sources
   - How to add new documentation sources
   - How to tune LLM models and prompts

---

## Future Enhancements (Beyond Phase 4)

- **Automated remediation execution** — Beyond suggesting remediation, actually execute safe actions (restart pods, scale deployments, clear caches)
- **Alert correlation** — Group related alerts into a single investigation
- **Learning from past investigations** — Use historical investigations to improve future root cause analysis
- **Multi-channel support output** — Post to Microsoft Teams, email, or custom webhooks
- **Custom toolsets** — Allow users to define custom investigation tools via configuration
- **Dashboard** — Streamlit dashboard for viewing investigations and support suggestions
- **Proactive monitoring** — Scheduled investigations that run before alerts fire (anomaly detection)
- **RAG pipeline** — Build a dedicated knowledge base from runbooks, postmortems, and documentation for richer context during investigations
