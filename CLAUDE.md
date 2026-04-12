# CLAUDE.md

See @AGENTS.md for coding conventions, patterns, and testing rules.
See @README.md for project overview, API endpoints, and architecture diagram.

## Essential Commands

```bash
just install          # Install dependencies with UV
just test             # Run unit tests
just test-integration # Run integration tests (requires DB)
just test-evals       # Run functional/E2E tests
just lint             # Ruff + MyPy + import-linter
just lint-fix         # Auto-format with Ruff
```

```bash
just test tests/unit/path/test_file.py           # Single test file
just test tests/unit/path/test_file.py::TestClass # Single test class
```

## Architecture (non-obvious)

- Layer boundaries enforced by import-linter contracts in `pyproject.toml` — lower layers cannot import higher layers
- `settings.py` → env vars (singleton via `get_settings()`), `config.py` → wires adapters (singleton via `get_config()`)
- Vendor adapters no-op when unconfigured (missing API keys) via `is_configured` property
- All LLM agents route through LiteLLM gateway (`AI_GATEWAY_URL`), model names are per-agent env vars
- Agents use `Agent("test", ...)` placeholder model, overridden at runtime via `.run(model=...)`

## Pipelines (gotchas)

- Pipeline nodes use `NodeError` / `PipelineNodeFailed` from `domain/pipeline/errors.py`
- `DetermineConfidence` enforces approval gate: below `require_approval_below_confidence` (default 0.7) requires human approval
- Webhook endpoints use shared `_handle_webhook()` for deduplication
- Agent system prompts are Jinja2 templates in `domain/prompts/`

## Testing

- 555+ tests across `tests/unit/`, `tests/integration/`, `tests/functional/`
- Factories in `tests/factories/__init__.py`: `make_alert()`, `make_ticket()`, `make_investigation()`, `make_finding()`, `make_doc_source()`, `make_response_suggestion()`, `make_confidence_score()`
- Functional tests monkeypatch PydanticAI agents — see `tests/functional/conftest.py`

## Auto-Commit

After completing a fix, feature, or refactor — commit automatically without asking:
1. Stage only the relevant changed files (no `git add -A`)
2. Write a conventional commit message (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, etc.)
3. If the work spans multiple logical changes, create separate atomic commits for each

## Documentation Workflow

### Source of truth

- **Requirements & status** — `docs/prd.md` (acceptance criteria checkboxes are the canonical tracker)
- **Architecture** — `docs/architecture.md` (design principles, layer diagram, pipeline flows, decisions, capability plane)
- **Plan status** — `docs/plans/INDEX.md` (read this first, not individual plans)
- **Architecture reviews** — `docs/reviews/` (frozen historical snapshots, do not update)

### Plan and review locations (overrides superpowers defaults)

- **Plans** go in `docs/plans/<feature-name>.md` — NOT `docs/superpowers/plans/`
- **Reviews** go in `docs/reviews/` — NOT anywhere else
- **Plan index** at `docs/plans/INDEX.md` — read this FIRST to understand plan status (saves tokens vs reading all plans)
- Use the template at `docs/plans/_template.md` for new plans

### For planned features (multi-step)

1. Check `docs/plans/INDEX.md` to see if a plan already exists
2. Create `docs/plans/<feature-name>.md` using the template at `docs/plans/_template.md`
3. Add an entry to `docs/plans/INDEX.md` under the appropriate status section
4. Fill in: goal, scope, design decisions, step-by-step plan with checkboxes
5. Get user confirmation before writing code
6. Check off steps as you go; update the plan if the approach changes
7. On completion: update status in both the plan file and `INDEX.md`, then run `/update-docs`

### For any completed work (including impromptu fixes)

After implementing ANY change that resolves a requirement or changes architecture:
1. Run `/update-docs` — it diffs recent commits against `docs/prd.md` and suggests updates
2. Review and apply the suggested checkbox updates
3. If the change affects architecture, update `docs/architecture.md`

### What NOT to update

- `docs/reviews/*` — frozen historical snapshots
- Status lives only in `docs/prd.md` (acceptance criteria) and `docs/plans/INDEX.md` (plan progress)

## Agent Teams

This project uses Claude Code Agent Teams for multi-agent coordination. Subagent definitions live in `.claude/agents/`.

### Available roles

| Role | File | Focus |
|------|------|-------|
| `platform-engineer` | `.claude/agents/platform-engineer.md` | Infrastructure, adapters, config, observability |
| `ai-engineer` | `.claude/agents/ai-engineer.md` | LLM pipelines, agents, prompts, evaluation |
| `product-engineer` | `.claude/agents/product-engineer.md` | API endpoints, data flow, user-facing features |
| `security-engineer` | `.claude/agents/security-engineer.md` | Auth, validation, OWASP, secrets management |

### Quality gates (automated via hooks)

- **TaskCompleted** hook runs `scripts/validate-task.sh` (ruff + unit tests). Exit code 2 blocks completion.
- **TeammateIdle** hook runs `scripts/run-qa.sh` (full lint + test suite). Exit code 2 keeps teammate working.

These hooks replace dedicated PM and QA agent sessions — same quality gates, no inference cost.

### Spawning a team

```text
Create an agent team for <task>. Spawn:
- A platform-engineer teammate for <infrastructure work>
- An ai-engineer teammate for <pipeline work>

Platform engineer starts with <task A>. AI engineer starts with <task B>,
then picks up <task C> which depends on platform engineer's output.
```

All teammates automatically load CLAUDE.md, AGENTS.md, and .claude/rules/ — do not repeat conventions in spawn prompts.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
