# Plan: K8s Agent & MCP Integration

**Status:** in-progress
**Created:** 2026-04-02
**Last updated:** 2026-04-03

## Goal

Implement two parallel K8s investigation backends (native PydanticAI agent and kagent) so we can compare their performance across accuracy, latency, and operational cost in a hedge fund compliance context. Simultaneously, add MCP integration in both directions — Sentinel as MCP server (exposing tools) and MCP client (consuming external tools). This resolves the remaining PRD gaps for MCP tool integration (Phase C) and advances the kagent pilot (Phase 3).

## Scope

### In scope

- `BaseInvestigationAdapter` hierarchy refactor (rename `BaseHolmesAdapter`, add K8s adapters)
- Native K8s PydanticAI agent with kubernetes Python client tools
- Kagent adapter delegating to kagent CRDs via K8s API
- Config-driven backend selection (`native`, `kagent`, `both` for comparison)
- MCP server exposing observability, documentation, and investigation tools via FastMCP
- MCP client consuming external MCP servers (e.g., kubectl MCP server)
- Pipeline-agnostic evaluation metrics (`domain/evaluation/`)
- Comparison framework for side-by-side scoring
- Helm chart updates (RBAC, kagent dependency, MCP server deployment, network policies)
- Kind/Minikube local dev setup with kagent operator

### Out of scope

- Automated remediation (Sentinel investigates only, humans act)
- AgentGateway adoption (deferred until multiple MCP backends justify it)
- Fine-tuning models for K8s-specific investigation
- Multi-tenant K8s investigation (single cluster target for v1)

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Adapter hierarchy | `BaseInvestigationAdapter` → `DirectToolsetAdapter` + `K8sInvestigationAdapter` → `NativeK8sAgent` / `KagentAdapter` | Proven adapter pattern in codebase; allows clean A/B comparison with shared audit trail |
| K8s native tools | Python `kubernetes` client + optional MCP consumption | Fast, typed, testable native tools; MCP extends reach without replacing core |
| Kagent integration | CRD-based delegation via K8s API, poll for completion | kagent's native model; no custom RPC or sidecar needed |
| Local dev for kagent | Kind/Minikube with kagent operator | Real integration avoids mock/prod divergence — critical for hedge fund confidence |
| MCP server transport | Streamable HTTP (separate deployment) | Deployable, discoverable, matches PydanticAI's MCPServerHTTP client |
| MCP server scope | Observability + documentation + investigation triggering | Full platform surface — makes Sentinel discoverable by other agents |
| MCP client | `plugins/toolsets/mcp.py` builder, injected at agent runtime | Follows existing toolset injection pattern; no domain changes needed |
| Evaluation metrics | Pipeline-agnostic `EvaluationMetrics` in `domain/evaluation/` | Reusable across any adapter or pipeline comparison, not just K8s |
| Comparison mode | `K8S_INVESTIGATION_BACKEND=both` runs adapters concurrently | Primary result flows through pipeline; secondary stored for comparison |
| K8s RBAC | Read-only: `get`, `list`, `watch` on core resources + `pods/log` | No mutations — hedge fund compliance requires minimal blast radius |

## Architecture

### Investigation Adapter Hierarchy

```
BaseInvestigationAdapter (ABC)           domain/sre/investigation.py
├── DirectToolsetAdapter (existing)      domain/sre/holmes_adapter.py
└── K8sInvestigationAdapter (ABC)        domain/sre/investigation.py
    ├── NativeK8sAgent                   domain/sre/k8s_native_agent.py
    └── KagentAdapter                    domain/sre/kagent_adapter.py
```

### Shared Contract

```python
@attrs.frozen
class InvestigationContext:
    cluster_name: str
    namespace: str | None  # None = cluster-wide
    additional_sources: tuple[str, ...] = ()  # Extra context hints

@attrs.frozen
class AuditEntry:
    timestamp: datetime
    adapter_name: str          # "native_k8s", "kagent", "holmes"
    action: str                # "tool_call", "http_request", "crd_operation"
    tool_name: str | None      # "get_pod_status", "query_recent_logs"
    status: str                # "success", "error", "timeout"
    duration_ms: int
    error_code: str | None     # HTTP status, K8s API error code
    payload: Mapping[str, Any] # Freeform: urls, params, response summaries

@attrs.frozen
class InvestigationResult:
    findings: tuple[Finding, ...]
    sources_queried: tuple[str, ...]
    duration_ms: int
    adapter_name: str  # "holmes", "native_k8s", "kagent"
    audit_trail: tuple[AuditEntry, ...]  # Every action taken during investigation

class BaseInvestigationAdapter(ABC):
    @abstractmethod
    async def investigate(self, *, alert: Alert, context: InvestigationContext) -> InvestigationResult: ...

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...
```

### K8s Native Agent

PydanticAI agent with two tool sources:

**Native Python tools** (`domain/tools/kubernetes.py` → `plugins/toolsets/kubernetes.py`):
- `get_pod_status(namespace, pod_name)` — phase, restart count, conditions
- `get_deployment_status(namespace, deployment_name)` — replica counts, rollout status
- `get_recent_events(namespace, resource_name)` — K8s events (warnings, errors)
- `get_pod_logs(namespace, pod_name, container, tail_lines)` — container logs
- `describe_resource(namespace, kind, name)` — generic describe for any resource

Uses official `kubernetes` async client. No-op when not in-cluster or unconfigured (existing vendor adapter pattern).

**Optional MCP tools** (via PydanticAI `MCPServerHTTP` or `MCPServerStdio`):
- Pluggable external kubectl MCP servers for extended operations
- Configured via `K8S_MCP_SERVER_URL` or `K8S_MCP_SERVER_COMMAND`
- When unconfigured, agent runs with native tools only

**Agent definition** follows existing patterns:
- Placeholder model `Agent("test", ...)`, overridden at runtime
- Jinja2 system prompt in `plugins/prompts/k8s_investigator.j2`
- Dependencies: alert context, cluster name, namespace
- Output: structured `K8sInvestigationOutput` with findings, affected resources, timeline

### Kagent Integration

```
Alert → KagentAdapter.investigate()
  → Create kagent CRD with alert context
  → Poll CRD status until completed/failed/timeout
  → Parse kagent findings → map to InvestigationResult
  → Return with adapter_name="kagent"
```

- Timeout: `KAGENT_INVESTIGATION_TIMEOUT_SECONDS` (default 120s)
- Failure: returns degraded `InvestigationResult` with low confidence → triggers approval gate
- CRD schema: thin wrapper — pass alert metadata and namespace
- Auth: Sentinel service account needs RBAC for kagent CRDs (`create`, `get`, `list`, `watch`)
- Kagent's raw output stored alongside mapped findings for audit traceability

### MCP Server

```
src/sentinel/interfaces/mcp/
├── server.py              # FastMCP app definition
├── tools/
│   ├── observability.py   # Wraps domain/tools/observability.py
│   ├── documentation.py   # Wraps domain/tools/documentation.py
│   └── investigation.py   # Trigger/query investigations
```

- FastMCP with streamable HTTP transport
- Tools are thin wrappers around existing `domain/tools/` — no logic duplication
- Investigation tools: `trigger_investigation()`, `get_investigation_status()`, `get_investigation_result()`
- Auth: API key validation middleware
- Separate deployment (Helm `mcpServer` block)
- All tool invocations logged with caller identity
- Rate limiting to prevent runaway external agents

### MCP Client

```
src/sentinel/plugins/toolsets/mcp.py
```

- `build_mcp_toolset(server_config)` returns PydanticAI-compatible `MCPServerHTTP` or `MCPServerStdio`
- Config via `MCP_SERVERS` env var (JSON list of `{name, url}` or `{name, command, args}`)
- Injected at agent runtime: `agent.run(toolsets=[native_tools, *mcp_toolsets])`
- K8s native agent uses this to optionally consume kubectl MCP server

### Pipeline-Agnostic Evaluation Metrics

```python
# domain/evaluation/metrics.py
@attrs.frozen
class EvaluationMetrics:
    factual_precision: float      # % findings matching expected root causes
    factual_recall: float         # % expected findings surfaced
    hallucination_rate: float     # % claims with no backing evidence
    latency_p50_ms: int
    latency_p95_ms: int
    latency_p99_ms: int
    confidence_brier_score: float # predicted vs actual accuracy calibration
    evidence_source_count: int
    evidence_diversity: float     # unique source types / total sources
    robustness_variance: float    # consistency across N retries
    degradation_score: float      # 1.0 = clean degradation, 0.0 = hallucinated
    token_cost: int

# domain/evaluation/comparison.py
@attrs.frozen
class ComparisonResult:
    case_id: str
    baseline: EvaluationMetrics
    challenger: EvaluationMetrics
    winner_by_dimension: Mapping[str, str]  # dimension → adapter_name (immutable)
```

Lives in `domain/evaluation/` — a domain concept reusable for any adapter or pipeline comparison.

### Helm Chart Updates

**New values:**

```yaml
k8sAgent:
  enabled: false
  backend: "native"  # "native", "kagent", or "both"
  rbac:
    create: true
    namespaces: []  # Empty = cluster-wide

kagent:
  enabled: false
  namespace: "kagent-system"
  investigationTimeout: 120

mcpServer:
  enabled: false
  transport: "streamable-http"
  port: 8811
  replicaCount: 1
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

**New templates:**
- `clusterrole.yaml` — read-only K8s API access (`get`, `list`, `watch` on pods, deployments, replicasets, events, services, nodes; `get` on `pods/log`). Conditionally includes kagent CRD verbs.
- `mcp-deployment.yaml` — MCP server deployment (when `mcpServer.enabled`)
- `mcp-service.yaml` — ClusterIP service for MCP server

**Updated templates:**
- `networkpolicy.yaml` — egress to K8s API server, kagent namespace, MCP server
- `serviceaccount.yaml` — annotations for K8s API access

### Audit Trail

Every adapter records an `AuditEntry` for each action taken during investigation. The design uses a **typed envelope + freeform payload** pattern:

- **Typed envelope** (`timestamp`, `adapter_name`, `action`, `tool_name`, `status`, `duration_ms`, `error_code`) — stable fields for querying, alerting, and compliance reporting
- **Freeform payload** (`Mapping[str, Any]`) — absorbs tool-specific detail (URLs checked, namespaces queried, CRD spec, response snippets, token counts) without schema changes
- New tools just write different keys into `payload` — no new classes or schema migrations needed

Each adapter appends entries as it works. The pipeline stores the full `audit_trail` tuple immutably alongside the investigation result. This complements PydanticAI's built-in `instrument=True` OpenTelemetry tracing with a domain-level audit record that is persisted and queryable.

### Streamlit Chat App Updates

The local testing chat app (`interfaces/chat/app.py`) gains:

**Sidebar configuration:**
- Investigation backend selector: dropdown for `Holmes`, `Native K8s`, `Kagent`, or `Both (comparison)` — maps to `K8S_INVESTIGATION_BACKEND`
- Cluster/namespace inputs when K8s backend is selected
- MCP server status indicator (connected/disconnected)

**Audit trail viewer:**
- New expandable section below agent traces: "Audit Trail"
- Renders each `AuditEntry` as a timeline — timestamp, tool name, status badge (green/red/yellow), duration
- Freeform payload shown as collapsible JSON
- In comparison mode, shows both backends' audit trails side-by-side

**K8s-specific test scenarios** (added to sidebar):
- Pod CrashLoopBackOff with OOMKilled (existing, now routed to K8s agent)
- Node NotReady with kubelet heartbeat timeout
- Deployment rollout stuck (new ReplicaSet scaling but old pods not terminating)
- PVC pending with no available PersistentVolume
- Service endpoint not ready (readiness probe failing after config change)
- HPA unable to scale (metrics-server unavailable)
- Ingress returning 404 (backend service selector mismatch)

**Comparison mode UI:**
- When `Both` is selected, shows results side-by-side in two columns
- Each column shows: adapter name, duration, confidence score, findings, audit trail
- Summary row at bottom: which adapter was faster, which had higher confidence

### Hedge Fund Compliance

- All K8s API calls logged via structlog with cluster/namespace context
- Read-only RBAC — no mutations from investigation agents
- Both adapter results stored immutably when running in comparison mode
- Low-confidence results from either backend trigger the existing approval gate
- Kagent raw output captured alongside mapped findings for regulatory traceability
- MCP server rate-limited and auth-gated
- Comparison mode is opt-in, production runs single backend
- Typed audit envelope enables consistent compliance queries across all adapters

## Steps

### Phase A: Foundation (adapter hierarchy + audit trail + evaluation metrics)
- [x] Step 1: Create `domain/sre/investigation.py` with `BaseInvestigationAdapter`, `K8sInvestigationAdapter`, `InvestigationResult`, `InvestigationContext`, `AuditEntry`
- [x] Step 2: Refactor `DirectToolsetAdapter` to implement `BaseInvestigationAdapter` (rename `BaseHolmesAdapter`), emit `AuditEntry` records
- [x] Step 3: Update all references to `BaseHolmesAdapter` across codebase and tests
- [x] Step 4: Create `domain/evaluation/metrics.py` with `EvaluationMetrics` and `domain/evaluation/comparison.py` with `ComparisonResult`
- [x] Step 5: Add `evaluation` layer to import-linter contracts in `pyproject.toml` (between `evals` and `domain`)
- [x] Step 6: Verify import-linter contracts pass, all existing tests green

### Phase B: K8s Native Agent
- [x] Step 7: Add `kubernetes` async client dependency to `pyproject.toml`
- [x] Step 8: Create `domain/tools/kubernetes.py` with K8s query tool functions
- [x] Step 9: Create `plugins/toolsets/kubernetes.py` wrapping tools as `FunctionToolset`
- [x] Step 10: Create `plugins/prompts/k8s_investigator.j2` system prompt template
- [x] Step 11: Create `interfaces/graphs/agents/k8s_investigator.py` PydanticAI agent
- [x] Step 12: Implement `NativeK8sAgent` in `domain/sre/k8s_native_agent.py`
- [x] Step 13: Add `K8S_INVESTIGATION_BACKEND` config and wire in `config.py`
- [x] Step 14: Unit tests for all new modules

### Phase C: MCP Integration
- [x] Step 15: Add `fastmcp` dependency to `pyproject.toml`
- [x] Step 16: Create `interfaces/mcp/server.py` FastMCP app with observability, documentation, investigation tools
- [x] Step 17: Create `plugins/toolsets/mcp.py` MCP client toolset builder
- [x] Step 18: Wire MCP client into K8s native agent (optional kubectl MCP server) — config.py builds toolsets from MCP_SERVERS + K8S_MCP_SERVER_URL, investigation tools wired to real DB
- [x] Step 19: Add `MCP_SERVERS` and `K8S_MCP_SERVER_URL` config vars
- [x] Step 20: Unit tests for MCP server tools and client builder

### Phase D: Kagent Integration
- [x] Step 21: Create `domain/sre/kagent_adapter.py` implementing `K8sInvestigationAdapter`
- [ ] Step 22: Add kagent CRD creation, polling, and result mapping — adapter exists but CRD integration marked "pending"
- [x] Step 23: Add `KAGENT_INVESTIGATION_TIMEOUT_SECONDS` config
- [ ] Step 24: Set up Kind/Minikube dev environment with kagent operator
- [ ] Step 25: Integration tests against local kagent

### Phase E: Comparison Framework
- [x] Step 26: Implement comparison mode in pipeline node (concurrent adapter execution)
- [x] Step 27: Create `tests/evals/datasets/k8s_investigation/` golden cases
- [x] Step 28: Create `evals/evaluators/comparison.py` side-by-side scoring using `EvaluationMetrics`
- [ ] Step 29: Extend `evals/reporting.py` and `evals/rendering.py` for `ComparisonReport` — deferred, existing reporting works
- [ ] Step 30: End-to-end comparison test with both backends — deferred, requires running LLM

### Phase F: Helm & Infrastructure
- [x] Step 31: Add `clusterrole.yaml` and `clusterrolebinding.yaml` templates
- [x] Step 32: Add `mcp-deployment.yaml` and `mcp-service.yaml` templates
- [x] Step 33: Update `values.yaml` with `k8sAgent`, `kagent`, `mcpServer` blocks
- [x] Step 34: Update `networkpolicy.yaml` with K8s API, kagent, MCP egress rules
- [ ] Step 35: Update documentation (`docs/prd.md`, `docs/architecture.md`, `docs/claude-plan.md`)

### Phase G: Streamlit Chat App
- [x] Step 36: Add investigation backend selector to sidebar (Holmes / Native K8s / Kagent / Both)
- [x] Step 37: Add cluster/namespace configuration inputs (shown when K8s backend selected)
- [x] Step 38: Add K8s-specific test scenarios to sidebar (Node NotReady, rollout stuck, PVC pending, HPA scaling failure, ingress 404, readiness probe failure)
- [x] Step 39: Implement audit trail viewer — timeline of `AuditEntry` records with status badges and collapsible payload JSON
- [x] Step 40: Implement comparison mode UI — side-by-side columns showing both backends' results, confidence, duration, and audit trails
- [x] Step 41: Wire backend selector to `K8S_INVESTIGATION_BACKEND` config and pass `InvestigationContext` to pipeline

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-02 | Initial design | — |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
