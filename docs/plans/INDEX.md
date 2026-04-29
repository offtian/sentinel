# Implementation Plans

> **Token-saving rule:** Read this index first. Only open individual plan files when actively working on that feature.

## Status Key

- **complete** — shipped and merged
- **in-progress** — actively being worked on
- **draft** — planned but not started
- **abandoned** — decided not to pursue

## Plans

### Complete

| Plan | Goal | Merged |
|------|------|--------|
| [graph-consumption-refactor](graph-consumption-refactor.md) | Agents read from `config.agent_for()` instead of module singletons | PR #5 |
| [universal-mcp-injection](universal-mcp-injection.md) | `MCP_SERVERS` as single source of truth for shared MCP toolsets | PR #6 |
| [k8s-chart-coding-agent](k8s-chart-coding-agent.md) | Spec for chart-coding pipeline (natural language to Helm charts) | PR #1 |
| [k8s-chart-coding-agent-implementation](k8s-chart-coding-agent-implementation.md) | Detailed implementation of chart-coding pipeline | PR #1 |
| [database-traceability-refactor](database-traceability-refactor.md) | DB migration and traceability refactoring | PR #2 |
| [holmesgpt](holmesgpt.md) | Formalize DirectToolsetAdapter, add K8s queries, HolmesGPT SDK integration | PR #TBD |
| [skills-runtime](skills-runtime.md) | On-disk Skills catalogue + runtime loader + config-driven agent wiring | PR #TBD |
| [graph-consumption-refactor](graph-consumption-refactor.md) | Agents read from `config.agent_for()` instead of module singletons | PR #TBD |
| [anthropic-prompt-caching](anthropic-prompt-caching.md) | Vendor-agnostic prompt caching on all agent system prompts | PR #14 |
| [prompt-versioning-and-replay](prompt-versioning-and-replay.md) | Prompt version/hash + pipeline run replay + re-execution | PR #15 |
| [k8s-agent-and-mcp-integration](k8s-agent-and-mcp-integration.md) | Dual K8s backends (native + kagent) with MCP server/client | PR #TBD |
| [sentinel-foundations-f1-config-layering](sentinel-foundations-f1-config-layering.md) | F1 layered config substrate on `BaseConfiguration` (Pydantic) + new env-vars + policy primitives + `TEAM_CONFIG_REFS` dispatch | PR #22 |
| [sentinel-foundations-f2-envelope](sentinel-foundations-f2-envelope.md) | F2 identity envelope: middleware mints `request_id`, webhook factories build `Envelope`, pipelines + spans + log contexts carry it through | PR #23 |
| [sentinel-foundations-f4-otel-langfuse-replay](sentinel-foundations-f4-otel-langfuse-replay.md) | F4 Phase A — OTel → Langfuse triple: 9 mandatory span attrs, MandatoryAttributesValidator, Langfuse OTLP exporter, local v3 docker-compose | PR #28 |
| [sentinel-foundations-f4-replay-bundle](sentinel-foundations-f4-replay-bundle.md) | F4 Phase B — RFC §3.8 ReplayBundle (tool + LLM I/O capture), replay CLI on the new shape, 30-run determinism CI, architecture docs | PR #29 |

### In Progress

| Plan | Goal | Progress | Notes |
|------|------|----------|-------|
| [grafana-metrics](grafana-metrics.md) | OTel metrics instrumentation for Grafana dashboards | 2/5 | Prometheus reader + basic metrics wired |
| [sentinel-hedgefund-foundations](sentinel-hedgefund-foundations.md) | Evolve current codebase into RFC-001 v0.4 foundations (config, identity, OTel→Langfuse→replay, LiteLLM proxy, runbooks, capability tokens, groundedness) | 7/9 phases | F1 + F2 + F3 + F4 + F5 + F7 complete; F6 in progress |
| [sentinel-foundations-f7-capability-tokens](sentinel-foundations-f7-capability-tokens.md) | F7 runbook grants enforced at toolset-wrapper boundary; cross-tenant + non-listed-tool rejection with audit_log row | complete | Branch `feat/sentinel-foundations-f7-capability-tokens`; R-TL-3 ticked |
| [sentinel-foundations-f5-litellm-proxy](sentinel-foundations-f5-litellm-proxy.md) | F5 LiteLLM proxy migration + ADR 0007 orchestration framework decision | complete | PR #30 merged |
| [sentinel-foundations-f6-runbook-catalog](sentinel-foundations-f6-runbook-catalog.md) | F6 runbook catalog + three-stage matcher (deterministic tag + small-LLM disambiguator on ties / zero-match + opt-in pgvector RAG fallback) + `extends:` composition + lifecycle/drift/flywheel + Confluence read-only render | ~85% (47+ / 68 items) | Branch `feat/sentinel-foundations-f6-runbook-catalog`; F6.A–F6.E + F6.G + F6.J–F6.N (most) + F6.K complete; F6.F (pipeline node), F6.J.6 (RAG tests), F6.L.4–L.6 (drift Slack/Justfile/tests), F6.M.6 (flywheel tests), F6.N.4 (Confluence ops doc), F6.H (docs), F6.I (ship) in flight |
| [pydanticai-langgraph-adoption](pydanticai-langgraph-adoption.md) | Migrate orchestration harness to LangGraph; support pipeline first, SRE/chart follow with own plans | 0/3 phases | Support migration in flight; ADR 0007 authored in PR #30 |

### Draft (Not Started)

| Plan | Goal | PRD Section |
|------|------|-------------|
| [langgraph-sre-migration](langgraph-sre-migration.md) | SRE sub-plan under umbrella `pydanticai-langgraph-adoption`: SRE pipeline → LangGraph workflow with W2 feature flag + interrupt()-based approval gate; introduces typed observability layer (gen_ai.* semconv, token/cost) consumed by both legacy chart and new SRE | RFC §2.3, §15.14 |
| [sentinel-data-and-domain-restructure](sentinel-data-and-domain-restructure.md) | Split `data/` into `sql/` + `primitives/`; split `domain/sre/` into `domain/alerts/` + `domain/investigations/`; rename pipeline labels | RFC §15.1 |
| [metrics-and-observability-wiring](metrics-and-observability-wiring.md) | Wire unwired OTel metrics, SRE approval persistence (token usage + LLM cost delivered in PR #18) | 4, 6 |
| [otel-telemetry-exporter](otel-telemetry-exporter.md) | OTLP exporter for PydanticAI spans (Logfire/Datadog) | 4, 7 |
| [llm-settings-to-config](llm-settings-to-config.md) | Move LLM model config into CommonConfiguration | Internal |
