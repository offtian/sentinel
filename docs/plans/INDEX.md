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

### In Progress

| Plan | Goal | Progress | Notes |
|------|------|----------|-------|
| [skills-runtime](skills-runtime.md) | On-disk Skills catalogue + runtime loader | 27/31 | Phases 1-5 complete; config-driven refactor remaining |
| [k8s-agent-and-mcp-integration](k8s-agent-and-mcp-integration.md) | Dual K8s backends (native + kagent) with MCP | 35/41 | Spec-level plan |
| [k8s-agent-mcp-implementation](k8s-agent-mcp-implementation.md) | Detailed implementation of K8s + MCP integration | 67/76 | Execution-level plan |
| [grafana-metrics](grafana-metrics.md) | OTel metrics instrumentation for Grafana dashboards | 2/5 | Prometheus reader + basic metrics wired |

### Draft (Not Started)

| Plan | Goal | PRD Section |
|------|------|-------------|
| [anthropic-prompt-caching](anthropic-prompt-caching.md) | Cache markers on agent system prompts via LiteLLM | 1, 2 |
| [otel-telemetry-exporter](otel-telemetry-exporter.md) | OTLP exporter for PydanticAI spans (Logfire/Datadog) | 4, 7 |
| [prompt-versioning-and-replay](prompt-versioning-and-replay.md) | Prompt version/hash + pipeline run replay | 6 |
| [llm-settings-to-config](llm-settings-to-config.md) | Move LLM model config into CommonConfiguration | Internal |
