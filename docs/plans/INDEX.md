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
| [langgraph-sre-migration](langgraph-sre-migration.md) | SRE pipeline → LangGraph: typed observability layer, interrupt-based approval gate, flag-gated cutover, Phase 7 cleanup | PR #35 |
| [slack-vendor-cleanup](slack-vendor-cleanup.md) | Restructure `vendors/slack.py` into typed package: consolidate Block Kit, typed event parsers, `AsyncSlackClient` wrapper, structured logging | PR #34 |
| [sentinel-foundations-f5-litellm-proxy](sentinel-foundations-f5-litellm-proxy.md) | F5 LiteLLM proxy migration + ADR 0007 orchestration framework decision | PR #30 |
| [sentinel-foundations-f6-runbook-catalog](sentinel-foundations-f6-runbook-catalog.md) | F6 runbook catalog + three-stage matcher (deterministic tag + small-LLM disambiguator on ties / zero-match + opt-in pgvector RAG fallback) + `extends:` composition + lifecycle/drift/flywheel + Confluence read-only render | PR #31 |
| [sentinel-foundations-f7-capability-tokens](sentinel-foundations-f7-capability-tokens.md) | F7 runbook grants enforced at toolset-wrapper boundary; cross-tenant + non-listed-tool rejection with audit_log row | PR #33 |
| [sentinel-foundations-f8-quality-gate](sentinel-foundations-f8-quality-gate.md) | F8 deterministic groundedness gate + AssessQuality LangGraph node + replay determinism CI fix | PR #37 |
| [sentinel-hedgefund-foundations](sentinel-hedgefund-foundations.md) | Evolve current codebase into RFC-001 v0.4 foundations (config, identity, OTel→Langfuse→replay, LiteLLM proxy, runbooks, capability tokens, groundedness) | F1–F8 all merged via PRs #22, #23, #28, #29, #30, #31, #33, #37 |

### In Progress

| Plan | Goal | Progress | Notes |
|------|------|----------|-------|
| [grafana-metrics](grafana-metrics.md) | OTel metrics instrumentation for Grafana dashboards | 2/5 | Prometheus reader + basic metrics wired |
| [pydanticai-langgraph-adoption](pydanticai-langgraph-adoption.md) | Migrate orchestration harness to LangGraph; support pipeline first, SRE/chart follow with own plans | 0/3 phases | Support migration in flight; ADR 0007 authored in PR #30 |

### Draft (Not Started)

| Plan | Goal | PRD Section |
|------|------|-------------|
| [sentinel-data-and-domain-restructure](sentinel-data-and-domain-restructure.md) | Split `data/` into `sql/` + `primitives/`; split `domain/sre/` into `domain/alerts/` + `domain/investigations/`; rename pipeline labels | RFC §15.1 |
| [metrics-and-observability-wiring](metrics-and-observability-wiring.md) | Wire unwired OTel metrics, SRE approval persistence (token usage + LLM cost delivered in PR #18) | 4, 6 |
| [otel-telemetry-exporter](otel-telemetry-exporter.md) | OTLP exporter for PydanticAI spans (Logfire/Datadog) | 4, 7 |
| [llm-settings-to-config](llm-settings-to-config.md) | Move LLM model config into CommonConfiguration | Internal |
