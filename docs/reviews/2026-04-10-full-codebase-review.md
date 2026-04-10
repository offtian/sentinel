# Full Codebase Review — 2026-04-10

> Frozen snapshot. Do not update.

## Health at a Glance

| Metric | Status |
|--------|--------|
| Unit tests | **545 passing** (6.95s) |
| Lint (ruff + mypy + import-linter) | **All clean** — 0 issues across 162 source files, 304 formatted, 3 contracts kept |
| Source files | 170 Python files, ~16K lines |
| Test files | 134 files |
| Git | Clean main branch, 7 merged PRs |

## PRD Completion: 51/65 acceptance criteria (78%)

| PRD Section | Done | Total | % |
|---|---|---|---|
| 1. AI SRE Pipeline | 8 | 11 | 73% |
| 2. AI Support Pipeline | 7 | 10 | 70% |
| 3. Infrastructure & Scalability | **10** | **10** | **100%** |
| 4. Observability & Feedback Loop | 5 | 9 | 56% |
| 5. Scheduled Automations | **7** | **7** | **100%** |
| 6. Compliance & Quality Gating | 10 | 13 | 77% |
| 7. Capability Platform | 4 | 5 | 80% |

## What's Shipped

1. **Both core pipelines are end-to-end functional** — SRE investigation (PagerDuty/Datadog → classify → investigate → analyse → confidence gate → publish to Slack/PD) and Support review (Jira → classify → search docs → draft → confidence gate) both work.

2. **Infrastructure is production-ready** — PostgreSQL job queue with `SKIP LOCKED`, worker with `--run-once` for CronJobs, Helm chart with HPA/PDB/network policies, CI/CD pipeline, Docker image, Alembic migrations. Section 3 is 100% complete.

3. **Compliance controls are solid** — Human approval gate, configurable confidence threshold, append-only audit log with SHA-256 hashes, supervisor quality gate, `gather(return_exceptions=True)` for fault tolerance. This is the hedge fund differentiator.

4. **Agent capability plane is largely delivered** — Skills catalogue with 6 seed runbooks, universal MCP injection via `Configuration.build_mcp_toolsets()`, FastMCP server with `list_skills` tool, config-driven agent wiring via `agent_for()`.

5. **Code quality is strong** — Zero lint/type issues, clean architecture enforced by import-linter contracts, immutable domain entities (`@attrs.frozen`), consistent adapter pattern with `is_configured` no-ops.

6. **Architecture decisions are well-documented and defensible** — The PRD directly answers Toby's three KubeCon questions with clear rationale and escape hatches.

## What's Missing (14 unchecked criteria)

### Theme 1: Production benchmarking (2 items — blocked on deployment)

- Investigation < 2min benchmark
- Review < 3min benchmark
- *These can't be measured until deployed. Not a code gap.*

### Theme 2: Observability/telemetry (4 items — biggest gap)

- OTel/Logfire exporter for PydanticAI spans (`instrument=True` is set but no exporter wired)
- Per-pipeline-run snapshot persistence (tracing tables exist but `ExecutionTracer` is pending)
- Token usage/cost recording per agent call
- Skill activation logging + audit persistence
- *Plan exists (`otel-telemetry-exporter.md`) but is draft status.*

### Theme 3: Prompt versioning & replay (3 items — compliance Phase D)

- `prompt_version` + `prompt_sha256` on agent/audit records
- Skill content hashes recorded alongside prompt hashes
- `replay_pipeline_run(run_id)` for regulator playback
- *Plan exists (`prompt-versioning-and-replay.md`) but is draft.*

### Theme 4: Prompt caching (2 items — cost optimisation)

- Anthropic prompt-cache markers on SRE agent prompts
- Same for Support agent prompts
- *Plan exists (`anthropic-prompt-caching.md`) but is draft.*

### Theme 5: Skill-driven selection (2 items — intelligence gap)

- Alert classifier output driving runbook skill selection
- Ticket classifier output driving response skill selection
- *Skills are loaded but not yet dynamically selected based on classifier output. The `SKILLS_BY_AGENT` config is static.*

### Theme 6: Bootstrap OTel (1 item)

- `bootstrap.initialise()` configuring OTLP exporter
- *File exists (`bootstrap_otel.py`) but not wired.*

## Plan Progress

| Plan | Progress | Risk |
|------|----------|------|
| skills-runtime | 27/31 (87%) | Low — config-driven refactor remaining |
| k8s-agent-mcp-implementation | 67/76 (88%) | Low — mostly done |
| k8s-agent-and-mcp-integration | 35/41 (85%) | Low — spec-level, tracks above |
| grafana-metrics | 2/5 (40%) | **Medium** — only Prometheus reader + basic metrics wired |
| anthropic-prompt-caching | Draft | Low — well-scoped |
| otel-telemetry-exporter | Draft | **Medium** — biggest observability gap |
| prompt-versioning-and-replay | Draft | **High** — compliance requirement, most complex remaining work |
| llm-settings-to-config | Draft | Low — internal cleanup |

## Code Concerns

1. **`interfaces/chat/app.py` is 932 lines** — exceeds the 800-line max guideline. Could extract into smaller modules.
2. **`sre_investigation.py` is 563 lines** — approaching the threshold but reasonable for a pipeline definition.
3. **Test count is 545, not 555+** as documented — the README/architecture docs claim 555+. Minor doc drift.

## Talking Points for Toby

### 1. Where are we?

We're at **78% of PRD acceptance criteria** with all infrastructure and both core pipelines fully functional. The remaining 22% clusters into three themes: observability telemetry, prompt versioning/replay for compliance, and cost optimisation via prompt caching. None of these block the core investigation or support workflows — they're operational maturity items.

### 2. What's production-ready today?

Both pipelines, the job queue, the approval gate, the audit trail, the Helm chart, and CI/CD. An on-call engineer could receive a PagerDuty alert and get an AI investigation summary in Slack today. A support engineer could get AI-drafted ticket responses with source attribution. The human-in-the-loop compliance controls (confidence-gated approval, append-only audit log with SHA-256 hashes) are in place.

### 3. What do we still need for compliance sign-off?

Three items from PRD Section 6:

- **Prompt versioning** — recording which exact prompt + model produced each investigation, so we can explain outputs to regulators
- **Skill content hashing** — proving which runbook was active for a given run
- **Pipeline replay** — ability to re-execute a historical investigation from its snapshot for regulator playback

These are all in the draft plan (`prompt-versioning-and-replay.md`). The database tables (`pipeline_runs`, `node_executions`, `agent_calls`) already exist — the gap is populating them with version metadata and building the replay function.

### 4. What about the KubeCon questions?

All three are answered in the PRD with implementation evidence:

- **Scheduled jobs**: Worker `--run-once` mode + K8s CronJobs. Shipped. Escape hatch to Temporal if needed.
- **Feedback loop**: Three-layer system (human feedback API, multi-factor confidence scoring, golden-case eval framework). All shipped. 545 tests passing including eval framework.
- **AgentGateway**: Deferred correctly — MCP adopted instead. Universal MCP injection shipped in PR #6. AgentGateway makes sense when/if we add a second agent runtime.

### 5. What should we prioritise next?

Recommended priority order:

1. **Finish skills-runtime** (4 remaining items) — unblocks dynamic skill selection, which makes investigations smarter
2. **OTel telemetry exporter** — gives us visibility into agent performance before production load
3. **Prompt versioning** — compliance requirement, builds on existing tracing tables
4. **Prompt caching** — cost optimisation, straightforward LiteLLM `extra_body` change
5. **Production deployment + benchmarking** — validates the <2min / <3min targets

### 6. What's the risk profile?

- **Low risk**: Core pipelines, infrastructure, compliance controls — all tested and stable
- **Medium risk**: Observability gap — we have structured logging and Sentry, but no distributed tracing export yet. If something goes wrong in production, debugging agent behaviour will be harder without OTel spans
- **Watch item**: `prompt-versioning-and-replay` is the most complex remaining work and the one most likely to surface design questions (how do we handle model version drift? what's the fidelity guarantee on replay?)

### 7. Technical highlights worth calling out

- **Zero lint/type issues** across 162 source files — mypy strict, ruff clean, import-linter enforcing layer boundaries
- **Pluggable observability** — Datadog in prod, Grafana (Prometheus/Loki/Tempo) in dev, auto-detected from environment
- **6 curated runbook skills** — `k8s-crashloop-runbook`, `database-connection-runbook`, `latency-spike-runbook`, `auth-error-response`, `rate-limit-response`, `chart-helm-best-practices`
- **Clean architecture enforced at build time** — not just convention, import-linter contracts fail CI if violated
