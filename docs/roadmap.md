# Sentinel Roadmap

## Current Status: Phase 1 Complete (Bootstrap + AI SRE Core)

### What's Been Built

- Repository scaffolding (pyproject.toml, Makefile, Dockerfile, docker-compose.yml)
- Clean architecture skeleton with import-linter contracts
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
- 29 passing unit tests
- CLAUDE.md for Claude Code integration

---

## Phase 2: AI SRE Polish + Deployment (Next)

### Must Implement

1. **HolmesGPT SDK Integration** (`domain/sre/holmes_adapter.py`)
   - Currently a placeholder - needs actual SDK integration once pydantic-ai dependency conflict is resolved upstream
   - Alternative: implement our own toolsets that mirror HolmesGPT's approach:
     - `DatadogToolset` - Query Datadog logs, metrics, and traces via `datadog-api-client`
     - `KubernetesToolset` - Query pod status, events, logs via `kubernetes` Python client
     - `PrometheusToolset` - Execute PromQL queries
   - These should implement a `BaseToolset` ABC that the adapter orchestrates

2. **Datadog Vendor Adapter** (`domain/vendor_adapters/datadog_client.py`)
   - Query logs: `POST /api/v2/logs/events/search`
   - Query metrics: `POST /api/v1/query`
   - Query traces: `POST /api/v2/spans/events/search`
   - Get monitor details: `GET /api/v1/monitor/{monitor_id}`
   - Wrap `datadog-api-client` SDK

3. **PagerDuty Vendor Adapter** (`domain/vendor_adapters/pagerduty.py`)
   - Add investigation notes to incidents: `POST /incidents/{id}/notes`
   - Get incident details: `GET /incidents/{id}`
   - Update incident status
   - Wrap `pdpyras` SDK

4. **PostgreSQL Persistence Layer** (`application/sre/persist.py`)
   - Save InvestigationRecord after pipeline completes
   - Query past investigations for the same service/alert type
   - Database connection management in FastAPI lifespan

5. **Slack Integration** (`vendors/slack.py`)
   - Wire up `post_investigation_summary()` into the PublishFindings graph node
   - Test with a real Slack workspace

6. **Infrastructure Setup**
   - Create ECR repositories in OctoCloud (sentinel, sentinel-helm)
   - Create IAM role with necessary permissions
   - Create KMS key for SOPS secret encryption
   - Create ACM certificate
   - Set up ktl-services-deployment application directory

7. **CI/CD Pipeline** (`.circleci/config.yml`)
   - Jobs: mypy, test-and-lint, publish-image, package_chart_and_deploy
   - Helm chart in `helm/sentinel/`

8. **Integration Tests**
   - Test webhook → pipeline → database persistence flow
   - Test with mock LLM responses (PydanticAI test mode)

---

## Phase 3: AI Support Agent Core

### Must Implement

1. **Jira Vendor Adapter** (`domain/vendor_adapters/jira.py`)
   - Fetch tickets via JQL: `GET /rest/api/3/search`
   - Read ticket details: `GET /rest/api/3/issue/{issueIdOrKey}`
   - Post internal comments: `POST /rest/api/3/issue/{issueIdOrKey}/comment`
   - Transition ticket status: `POST /rest/api/3/issue/{issueIdOrKey}/transitions`
   - Handle Service Desk specifics (customer vs internal comments)
   - Wrap `jira` or `atlassian-python-api` SDK

2. **Confluence Vendor Adapter** (`domain/vendor_adapters/confluence.py`)
   - Search via CQL: `GET /wiki/rest/api/content/search`
   - Get page content: `GET /wiki/rest/api/content/{id}?expand=body.storage`
   - Convert storage format to plain text for LLM consumption
   - Handle space-scoped searches
   - Wrap `atlassian-python-api` SDK

3. **Document Search Implementations**
   - `NotionSearcher` implementing `BaseDocumentSearcher` - query via Bedrock KB or S3
   - `ConfluenceSearcher` implementing `BaseDocumentSearcher` - query via CQL
   - `S3DocumentSearcher` implementing `BaseDocumentSearcher` - direct S3 retrieval
   - `JiraPastTicketSearcher` implementing `BasePastTicketSearcher` - JQL for resolved tickets

4. **Jira Webhook Handler** (`interfaces/webhooks/jira.py`)
   - Separate module from the router for clean parsing
   - Parse Jira webhook payload into Ticket entity
   - Handle issue_created and issue_updated events
   - Filter by project/issue type to avoid processing irrelevant tickets

5. **Wire Up Search in Pipeline**
   - Connect document and ticket searchers to the `SearchDocumentation` graph node
   - Configure which searchers are active via feature flags
   - Parallel search execution with error isolation (one failing searcher doesn't break the flow)

6. **Unit Tests**
   - Jira webhook parsing tests
   - Jira/Confluence vendor adapter tests (with mocked HTTP responses)
   - Search implementation tests
   - Support pipeline integration test with mock agents

---

## Phase 4: Polish + E2E

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

- **Automated remediation execution** - Beyond suggesting remediation, actually execute safe actions (restart pods, scale deployments, clear caches)
- **Alert correlation** - Group related alerts into a single investigation
- **Learning from past investigations** - Use historical investigations to improve future root cause analysis
- **Multi-channel support output** - Post to Microsoft Teams, email, or custom webhooks
- **Custom toolsets** - Allow users to define custom investigation tools via configuration
- **Dashboard** - Streamlit dashboard for viewing investigations and support suggestions
- **Proactive monitoring** - Scheduled investigations that run before alerts fire (anomaly detection)
- **RAG pipeline** - Build a dedicated knowledge base from runbooks, postmortems, and documentation for richer context during investigations
