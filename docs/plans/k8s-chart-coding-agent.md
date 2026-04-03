# Plan: K8s Chart Coding Agent

**Status:** complete
**Created:** 2026-04-03
**Last updated:** 2026-04-03

## Goal

Build a PydanticAI-powered coding agent that takes natural language requests, extracts a structured service specification, applies team-level policies, generates Helm charts, validates them, and commits the output to a GitOps directory — all within Sentinel's existing pipeline architecture.

## Scope

### In scope

- Streamlit UI for natural language chart requests
- NL request parsing into structured `ChartSpec` via PydanticAI agent
- Team policy registry (YAML files in `policies/`)
- Flat user-to-team mapping (`policies/_teams.yaml`)
- Helm chart generation via PydanticAI agent (Deployment, Service, HPA, NetworkPolicy, ArgoCD app)
- Static validation (`helm template` + `kubeconform`)
- Configurable policy validation (auto or human-gated via `K8S_CHART_AUTO_VALIDATE`)
- Configurable sandbox dry-run (auto or human-gated via `K8S_CHART_AUTO_SANDBOX`)
- Self-healing loop for syntax/schema errors (max 3 retries)
- Human escalation for policy violations
- Confidence scoring (weighted multi-factor)
- Output to `gitops/charts/<service-name>/`
- PR creation against the sentinel repo
- Evaluation framework (5 golden test cases, 3 evaluators)

### Out of scope

- Slack integration (Streamlit only for v1)
- Full sandbox deployment to ephemeral namespaces (v1 uses `helm install --dry-run`)
- Jinja2 template-based generation (v1 is LLM-generated; templates are a v2 optimisation)
- ArgoCD installation or cluster setup
- Okta/LDAP team resolution (v1 uses a flat mapping file)
- Kyverno/OPA policy engine integration
- Multi-cluster support
- Chart versioning and upgrade path generation

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Generation strategy | Approach B: Pydantic Graph pipeline with LLM generation (v1), evolving to Approach C: hybrid template-first + LLM fallback (v2) | Approach B ships fastest and matches existing SRE/Support pipeline architecture. Validation + self-heal loop mitigates LLM unpredictability. Approach A (pure Jinja2 templates) rejected because chart requests will include unusual configurations templates cannot anticipate. Approach C deferred to v2 when real usage data shows which resources to templatise. |
| Domain model base class | Pydantic `BaseModel` | Matches existing domain entities (`Alert`, `Investigation`) and is required for PydanticAI agent output types. The spec originally proposed `attrs.frozen` but the codebase uses Pydantic. |
| Policy storage | YAML files in `policies/` directory, version-controlled | Auditable via PRs (compliance requirement), no external dependency, testable, evolvable to database/Okta later. Interface is `resolve_team(user_id) -> str` — backend-swappable. |
| Error handling strategy | Syntax errors: self-heal silently (max 3 retries). Policy violations: escalate to human. | Syntax errors are the agent's problem. Policy conflicts are business decisions that need human judgement. |
| Approval gate | Reuse existing `REQUIRE_APPROVAL_BELOW_CONFIDENCE` threshold (default 0.7). Separate booleans for policy validation and sandbox. | Consistent with SRE pipeline. Low-confidence charts always require human review. |
| Output location | `gitops/charts/` in sentinel repo root | Self-contained for v1. Clean cut to a separate GitOps repo later (just move the directory). |

### Generation Approaches Evaluated

**Approach A: PydanticAI Agent + Jinja2 Templates** — LLM extracts spec, Jinja2 templates render charts deterministically. Pros: predictable, cheap, fast. Cons: rigid, cannot handle unusual requests. Rejected for v1.

**Approach B: Pydantic Graph Pipeline with LLM Generation** — Multi-node pipeline where LLM generates raw YAML. Validation catches mistakes, self-heal loop fixes them. Pros: flexible, consistent with codebase, testable nodes. Cons: less deterministic, higher token cost. **Chosen for v1.**

**Approach C: Hybrid — Template-First, LLM Fallback** — Jinja2 for known resources, LLM for unusual ones. Pros: deterministic for common cases, flexible for edge cases. Cons: two code paths, fuzzy boundary. **Planned for v2** once usage data identifies common resources to templatise.

## Architecture

### Pipeline

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
| MergeSpec | ChartSpec + TeamPolicy | ChartSpec (validated) or PolicyViolation[] | No | Applies defaults, detects conflicts |
| GenerateChart | merged ChartSpec | ChartOutput | Yes | PydanticAI agent generates Helm chart YAML files |
| ValidateChart | ChartOutput | ValidationResult | No | Runs helm template + kubeconform |
| ApprovalGate | ChartOutput | ChartOutput (approved) | No | Configurable auto/manual |
| CommitToGitOps | approved ChartOutput | PR URL | No | Writes to gitops/charts/, creates branch, opens PR |

### Domain Model

Uses Pydantic `BaseModel` (matching existing domain entities):

- `ChartRequest` — raw user input (requester, team, raw_message, requested_at)
- `ChartSpec` — structured spec (service_name, image, ports, resources, replicas, dependencies, env vars, run_as_non_root, extra_resources)
- `TeamPolicy` — loaded from YAML (team, namespace, max_memory, max_cpu, max_replicas, require_network_policy, require_non_root, allowed_egress, default_labels)
- `ChartOutput` — generated result (service_name, files, validation_result, policy_violations, generation_attempts, confidence_score)
- `PolicyViolation` — field, requested, allowed, message
- `ValidationResult` — helm_template_ok, kubeconform_ok, errors, warnings
- Supporting types: `PortSpec`, `ResourceSpec`, `ReplicaSpec`, `DependencySpec`, `EnvVarSpec`, `EgressRule`, `GeneratedFile`

### PydanticAI Agents

- `chart_request_parser` — extracts ChartSpec from NL. No tools. System prompt: `plugins/prompts/chart_request_parser.j2`
- `chart_generator` — produces Helm chart files from validated ChartSpec. Tools: `get_team_policy`, `get_chart_template_example`. System prompt: `plugins/prompts/chart_generator.j2`

### Confidence Scoring

| Factor | Weight | Measurement |
|---|---|---|
| Schema validity | 0.3 | kubeconform pass = 1.0, fail = 0.0 |
| Template rendering | 0.2 | helm template success = 1.0, warnings = 0.5, fail = 0.0 |
| Policy compliance | 0.25 | no violations = 1.0, auto-resolved = 0.7, escalated = 0.0 |
| Spec coverage | 0.15 | % of requested resources generated |
| Retry count | 0.1 | 0 retries = 1.0, 1 = 0.7, 2 = 0.4, 3 = 0.1 |

### Settings

| Variable | Description | Default |
|---|---|---|
| `K8S_CHART_GENERATOR_LLM` | Model for chart generation | `openai/gpt-4.1` |
| `K8S_CHART_PARSER_LLM` | Model for request parsing | `openai/gpt-4.1-mini` |
| `K8S_CHART_AUTO_VALIDATE` | Auto-approve policy validation | `False` |
| `K8S_CHART_AUTO_SANDBOX` | Auto-run sandbox dry-run | `False` |
| `K8S_CHART_SANDBOX_CONTEXT` | Kubeconfig context for sandbox | `""` |
| `K8S_CHART_MAX_RETRIES` | Self-heal retry limit | `3` |

### File Locations

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
| Parser prompt | `src/sentinel/plugins/prompts/chart_request_parser.j2` |
| Generator prompt | `src/sentinel/plugins/prompts/chart_generator.j2` |
| Settings | `src/sentinel/settings.py` (extend existing) |
| Policy files | `policies/` |
| Generated output | `gitops/charts/` |
| Eval cases | `src/sentinel/evals/cases/chart_generation.py` |
| Evaluators | `src/sentinel/evals/evaluators/chart_evaluators.py` |
| Streamlit UI | `src/sentinel/interfaces/chat/app.py` (extend existing) |

## Steps

- [x] Step 1: Add chart agent settings to `settings.py`
- [x] Step 2: Create domain types (`domain/charts/entities.py`)
- [x] Step 3: Create policy registry (`policies/` YAML files + `domain/charts/policies.py`)
- [x] Step 4: Create chart request parser agent (`agents/chart_request_parser.py` + prompt)
- [x] Step 5: Create chart generator agent (`agents/chart_generator.py` + prompt)
- [x] Step 6: Create validation runner (`domain/charts/validation.py`)
- [x] Step 7: Create confidence scoring (`domain/charts/confidence.py`)
- [x] Step 8: Create pipeline graph (`interfaces/graphs/chart_generation.py`)
- [x] Step 9: Create GitOps committer (`application/charts/commit.py`)
- [x] Step 10: Wire into config.py
- [x] Step 11: Add Streamlit UI section
- [x] Step 12: Create evaluation framework (cases + evaluators)
- [x] Step 13: Run end-to-end validation and fix issues

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-03 | Domain model switched from `attrs.frozen` to Pydantic `BaseModel` | Codebase exploration revealed existing entities use Pydantic, and PydanticAI requires Pydantic models for agent output types |
| 2026-04-03 | Prompt files use `.j2` extension (not `.yml`) | Matches existing prompt templates in `plugins/prompts/` |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- Evolve to Approach C (template-first + LLM fallback) based on usage data
- Slack integration as input channel
- Okta/LDAP team resolution
- Full sandbox deployment to ephemeral namespaces
- Kyverno/OPA policy engine integration
