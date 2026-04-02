# K8s Chart Coding Agent — Design Spec

## Goal

Build a PydanticAI-powered coding agent that takes natural language requests, extracts a structured service specification, applies team-level policies, generates Helm charts, validates them, and commits the output to a GitOps directory — all within Sentinel's existing pipeline architecture.

## Non-Goals (v1)

- Slack integration (Streamlit only for v1)
- Full sandbox deployment to ephemeral namespaces (v1 uses `helm install --dry-run`)
- Jinja2 template-based generation (v1 is LLM-generated; templates are a v2 optimisation)
- ArgoCD installation or cluster setup
- Okta/LDAP team resolution (v1 uses a flat mapping file)

---

## Design Decisions: Generation Strategy

We evaluated three approaches for how the agent produces Helm charts:

### Approach A: PydanticAI Agent + Jinja2 Templates

A single PydanticAI agent parses the NL request into a structured `ChartSpec`, then renders Helm chart files through deterministic Jinja2 templates. The LLM only does extraction — generation is template-driven.

- **Pros:** Deterministic output, fast, cheap (one LLM call), easy to test
- **Cons:** Rigid — every new resource type needs a new template. Cannot handle unusual requests ("add a custom CronJob sidecar that does X")

### Approach B: Pydantic Graph Pipeline with LLM Generation

A multi-node pipeline (matching Sentinel's SRE/Support pattern) where the LLM generates raw YAML. Validation catches mistakes, a self-heal loop fixes them.

- **Pros:** Consistent with Sentinel's architecture, flexible, each node independently testable, self-heal loop is a natural graph edge
- **Cons:** Less deterministic, higher token cost, risk of hallucinated K8s fields (mitigated by kubeconform + self-heal)

### Approach C: Hybrid — Template-First, LLM Fallback

Combines A and B. Jinja2 templates handle known resource types (Deployment, Service, HPA, NetworkPolicy). Unknown or unusual resources fall back to LLM generation.

- **Pros:** Deterministic for the 90% case, flexible for the 10%, cost-efficient
- **Cons:** Most complex, two code paths to maintain, fuzzy boundary between template-able and needs-LLM

### Decision: Phased B → C

**v1 uses Approach B** — pipeline + LLM generation. It ships fastest and is architecturally consistent with the codebase. The validation + self-heal loop mitigates LLM unpredictability.

**v2 evolves to Approach C** — once real usage data shows which resources are requested most often and where the LLM struggles, introduce Jinja2 templates for those common cases and shift the LLM to a fallback role.

Approach A was rejected outright because chart requests will include unusual configurations that templates cannot anticipate.

---

## Architecture: Pydantic Graph Pipeline

The agent is a Pydantic Graph DAG, consistent with the SRE and Support pipelines:

```
ParseRequest → LoadPolicy → MergeSpec → GenerateChart → ValidateChart → [ApprovalGate] → CommitToGitOps
                                              ^                |
                                              └── self-heal ───┘ (syntax errors, max 3 retries)
                                                     |
                                              escalate (policy violations)
```

### Node Breakdown

| Node | Input | Output | LLM? | Description |
|---|---|---|---|---|
| ParseRequest | ChartRequest | ChartSpec | Yes | PydanticAI agent extracts structured spec from NL |
| LoadPolicy | ChartSpec.team | TeamPolicy | No | Reads `policies/<team>.yaml`, raises NodeError if unknown |
| MergeSpec | ChartSpec + TeamPolicy | ChartSpec (validated) or PolicyViolation[] | No | Applies defaults, detects conflicts. Auto-resolves fixable issues, escalates business conflicts |
| GenerateChart | merged ChartSpec | ChartOutput | Yes | PydanticAI agent generates Helm chart YAML files |
| ValidateChart | ChartOutput | ValidationResult | No | Runs helm template + kubeconform. Syntax failure loops back (max 3). Pass continues |
| ApprovalGate | ChartOutput | ChartOutput (approved) | No | Configurable auto/manual. Score below confidence threshold forces manual |
| CommitToGitOps | approved ChartOutput | PR URL | No | Writes to gitops/charts/<service>/, creates branch, opens PR |

### Error Handling

Same `NodeError` / `PipelineNodeFailed` pattern as the SRE pipeline. Each node catches its own errors and wraps them with context.

### Self-Heal Loop

On validation failure:
- **Syntax/schema errors** — loop back to GenerateChart with the error message appended to the prompt. Max 3 retries, then fail with NodeError.
- **Policy violations** — escalate to human via Streamlit. Never auto-resolve business decisions.

---

## Domain Model

All types use `@attrs.frozen` for immutability.

### ChartRequest

The raw user input.

```python
@attrs.frozen
class ChartRequest:
    requester: str          # Slack user ID or Streamlit session
    team: str               # resolved team identifier
    raw_message: str        # natural language request
    requested_at: datetime
```

### ChartSpec

Structured specification extracted by the LLM.

```python
@attrs.frozen
class PortSpec:
    name: str
    container_port: int
    service_port: int
    protocol: str  # TCP | UDP

@attrs.frozen
class ResourceSpec:
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str

@attrs.frozen
class ReplicaSpec:
    min_replicas: int
    max_replicas: int
    target_cpu_percent: int

@attrs.frozen
class DependencySpec:
    name: str
    host: str
    port: int

@attrs.frozen
class EnvVarSpec:
    name: str
    value: str | None       # plain value
    secret_ref: str | None  # K8s secret reference

@attrs.frozen
class ChartSpec:
    service_name: str
    image: str
    ports: tuple[PortSpec, ...]
    resources: ResourceSpec
    replicas: ReplicaSpec
    dependencies: tuple[DependencySpec, ...]
    environment_variables: tuple[EnvVarSpec, ...]
    run_as_non_root: bool
    extra_resources: tuple[str, ...]  # free-text for LLM fallback
```

### TeamPolicy

Loaded from `policies/<team>.yaml`.

```python
@attrs.frozen
class EgressRule:
    name: str
    host: str
    port: int

@attrs.frozen
class TeamPolicy:
    team: str
    namespace: str
    max_memory: str
    max_cpu: str
    max_replicas: int
    require_network_policy: bool
    require_non_root: bool
    allowed_egress: tuple[EgressRule, ...]
    default_labels: tuple[tuple[str, str], ...]
```

### ChartOutput

The generated result.

```python
@attrs.frozen
class GeneratedFile:
    path: str       # relative path, e.g. "templates/deployment.yaml"
    content: str    # file content

@attrs.frozen
class PolicyViolation:
    field: str      # e.g. "resources.limits.memory"
    requested: str
    allowed: str
    message: str

@attrs.frozen
class ValidationResult:
    helm_template_ok: bool
    kubeconform_ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

@attrs.frozen
class ChartOutput:
    service_name: str
    files: tuple[GeneratedFile, ...]
    validation_result: ValidationResult | None
    policy_violations: tuple[PolicyViolation, ...]
    generation_attempts: int
    confidence_score: float
```

---

## PydanticAI Agents

Two agents, both following Sentinel's pattern of `Agent("test", ...)` with runtime model override via settings.

### chart_request_parser

- **Location:** `interfaces/graphs/agents/chart_request_parser.py`
- **Purpose:** Extract ChartSpec from natural language
- **System prompt:** `plugins/prompts/chart_request_parser.yml` (Jinja2 template)
- **Result type:** ChartSpec
- **Tools:** None (pure extraction)

### chart_generator

- **Location:** `interfaces/graphs/agents/chart_generator.py`
- **Purpose:** Produce Helm chart YAML files from a validated ChartSpec
- **System prompt:** `plugins/prompts/chart_generator.yml` (Jinja2 template). Includes Helm conventions, K8s best practices, full ChartSpec + TeamPolicy as context. On retry, includes the previous validation error.
- **Result type:** ChartOutput
- **Tools:**
  - `get_team_policy` — retrieve full policy for reference
  - `get_chart_template_example` — retrieve example chart structure (from `helm/sentinel/`)

---

## Policy Registry

### Policy files

Stored at `policies/` in the repo root. Each team gets a YAML file.

```yaml
# policies/trading-infra.yaml
team: trading-infra
namespace: trading
max_memory: "512Mi"
max_cpu: "500m"
max_replicas: 8
require_network_policy: true
require_non_root: true
allowed_egress:
  - name: postgres
    host: "postgres.trading.svc.cluster.local"
    port: 5432
  - name: redis
    host: "redis.trading.svc.cluster.local"
    port: 6379
default_labels:
  tier: critical
  compliance: sox
```

### Default policy

`policies/_default.yaml` — applied when a team has no specific file. Secure defaults: non-root required, network policy required, conservative resource limits.

### Team resolution

`policies/_teams.yaml` — flat user-to-team mapping for v1:

```yaml
users:
  U12345: trading-infra
  U67890: internal-tooling
default_team: internal-tooling
```

Interface: `resolve_team(user_id: str) -> str`. Swappable for Okta/LDAP later.

### MergeSpec logic

1. Apply `default_labels` from policy to spec
2. Enforce `require_non_root` — override spec if policy demands it
3. Check resource limits — if spec exceeds caps, create PolicyViolation and escalate
4. Check replica limits — same
5. If `require_network_policy` and no dependencies listed, flag warning (deny-all egress)

---

## Validation & Sandbox

### Static validation (always runs, autonomous)

1. `helm template` — renders chart, confirms valid YAML
2. `kubeconform` — validates against K8s API schemas
3. Failure → loop back to GenerateChart with error (max 3 retries)

### Policy validation (configurable)

- `K8S_CHART_AUTO_VALIDATE = False` (default): human approves in Streamlit
- `K8S_CHART_AUTO_VALIDATE = True`: auto-approved if no PolicyViolation. Violations always escalate.

### Sandbox deployment (configurable)

- `K8S_CHART_AUTO_SANDBOX = False` (default): skipped
- `K8S_CHART_AUTO_SANDBOX = True`: runs `helm install --dry-run` against sandbox context
- Requires `K8S_CHART_SANDBOX_CONTEXT` setting

---

## Confidence Scoring

Reuses Sentinel's existing `ConfidenceScore` domain type.

| Factor | Weight | Measurement |
|---|---|---|
| Schema validity | 0.3 | kubeconform pass = 1.0, fail = 0.0 |
| Template rendering | 0.2 | helm template success = 1.0, warnings = 0.5, fail = 0.0 |
| Policy compliance | 0.25 | no violations = 1.0, auto-resolved = 0.7, escalated = 0.0 |
| Spec coverage | 0.15 | % of requested resources generated |
| Retry count | 0.1 | 0 retries = 1.0, 1 = 0.7, 2 = 0.4, 3 = 0.1 |

Score below `REQUIRE_APPROVAL_BELOW_CONFIDENCE` (default 0.7) forces human approval regardless of auto-validate setting.

---

## Evaluation Framework

Following the existing `evals/` pattern with `pydantic_evals`.

### Golden test cases

| Case | Expected outcome |
|---|---|
| Basic FastAPI service with Postgres | Deployment, Service, NetworkPolicy with Postgres egress |
| High-scale service, 2-50 replicas | HPA with correct min/max |
| Trading-infra request exceeding memory cap | PolicyViolation raised |
| Minimal request, no dependencies | Deployment + Service only, deny-all egress if policy requires |
| Request with unknown custom resource | extra_resources populated, LLM handles it |

### Evaluators

- `ChartStructureEvaluator` — all expected files exist with correct K8s resource kinds
- `PolicyComplianceEvaluator` — generated chart respects team policy constraints
- `SpecCoverageEvaluator` — every requested resource type appears in output

---

## Output Structure

Written to `gitops/charts/<service-name>/`:

```
gitops/charts/order-processor/
├── Chart.yaml
├── values.yaml
├── values-dev.yaml
├── values-prod.yaml
├── templates/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── hpa.yaml
│   ├── networkpolicy.yaml    # only if policy requires it
│   └── _helpers.tpl
└── argocd-app.yaml
```

### PR creation

- Branch: `chart/<service-name>-<timestamp>`
- Commits all generated files
- PR body: original request, extracted spec, team policy applied, validation results, confidence score
- Follows Sentinel's existing PR format (Summary + Test plan)

---

## Settings

Added to `settings.py`:

| Variable | Description | Default |
|---|---|---|
| `K8S_CHART_GENERATOR_LLM` | Model for chart generation agent | `openai/gpt-4.1` |
| `K8S_CHART_PARSER_LLM` | Model for request parsing agent | `openai/gpt-4.1-mini` |
| `K8S_CHART_AUTO_VALIDATE` | Auto-approve policy validation | `False` |
| `K8S_CHART_AUTO_SANDBOX` | Auto-run sandbox dry-run | `False` |
| `K8S_CHART_SANDBOX_CONTEXT` | Kubeconfig context for sandbox | `""` |
| `K8S_CHART_MAX_RETRIES` | Self-heal retry limit | `3` |

---

## Streamlit UI

Extends the existing chat app at `src/sentinel/interfaces/chat/app.py`.

### Sidebar

- "K8s Chart Generator" section with example scenario buttons:
  - "Basic FastAPI service"
  - "Trading service with strict policy"
  - "High-scale worker with Redis + Postgres"
- Model picker for chart generator LLM

### Chat flow

1. User types NL request
2. Expander: "Extracted Specification" (ChartSpec)
3. Expander: "Team Policy Applied" (TeamPolicy)
4. If policy violations: warning cards with "Adjust and retry?" / "Escalate" buttons
5. Validation results display
6. If manual approval: "Approve" / "Reject" buttons
7. On approval: generated files in syntax-highlighted code blocks + "Create PR" button

### Audit trail

Each pipeline node logged as an AuditEntry (timestamp, node name, input/output, confidence score). Viewable in sidebar expander, reusing existing audit trail rendering.

---

## File Locations

| Component | Path |
|---|---|
| Pipeline graph | `src/sentinel/interfaces/graphs/chart_generation.py` |
| Parser agent | `src/sentinel/interfaces/graphs/agents/chart_request_parser.py` |
| Generator agent | `src/sentinel/interfaces/graphs/agents/chart_generator.py` |
| Domain types | `src/sentinel/domain/charts/entities.py` |
| Policy loader | `src/sentinel/domain/charts/policies.py` |
| Confidence scoring | `src/sentinel/domain/charts/confidence.py` |
| Validation runner | `src/sentinel/domain/charts/validation.py` |
| GitOps committer | `src/sentinel/application/charts/commit.py` |
| Parser prompt | `src/sentinel/plugins/prompts/chart_request_parser.yml` |
| Generator prompt | `src/sentinel/plugins/prompts/chart_generator.yml` |
| Settings | `src/sentinel/settings.py` (extend existing) |
| Policy files | `policies/` |
| Generated output | `gitops/charts/` |
| Eval cases | `src/sentinel/evals/cases/chart_generation.py` |
| Evaluators | `src/sentinel/evals/evaluators/chart_evaluators.py` |
| Streamlit UI | `src/sentinel/interfaces/chat/app.py` (extend existing) |

---

## v2 Roadmap (out of scope for v1)

- Evolve to Approach C: Jinja2 templates for common resources, LLM fallback for unusual ones (see Design Decisions section)
- Slack integration as input channel
- Okta/LDAP team resolution
- Full sandbox deployment to ephemeral namespaces
- Kyverno/OPA policy engine integration for validation
- Multi-cluster support
- Chart versioning and upgrade path generation
