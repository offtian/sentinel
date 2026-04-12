# Plan: Graph Consumption Refactor

**Status:** complete
**Created:** 2026-04-09
**Last updated:** 2026-04-10

## Goal

Make pipeline graph nodes read agents from `Configuration.agent_for()` instead of importing module-level singletons. This completes Steps 28-30 of the skills-runtime plan, removing the legacy `agent = build_agent()` fallback and dead skill-wiring code from agent modules.

## Scope

### In scope
- Step 28: Update graph Dependencies to carry `config` instead of individual `*_model` fields; nodes call `config.agent_for(name)` instead of `<module>.agent`
- Step 29: Remove module-level `agent = build_agent()` fallback from all 8 agent modules
- Step 30: Remove dead `append_skills_to_prompt` / `render_skills_section` from agent utils
- Update all functional and unit tests to mock via `config.agent_for()` instead of patching `<module>.agent`
- Update callers (worker, chat app, slack handlers) to pass `config` instead of model strings

### Out of scope
- Changing `build_agent()` factories themselves
- Modifying the `Configuration.load_agents()` method
- Agent runtime behaviour changes

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Dependencies field name | `config: Configuration` | Consistent with how other infra is wired; single field replaces N model fields |
| Agent lookup in nodes | `ctx.deps.config.agent_for("name")` | Uses the existing registry API; no new abstraction needed |
| Model override for chat UI | Keep `config` approach, UI builds its own Configuration | Chat app already has access to settings; can override via config |

## Steps

- [x] Step 1: Update `sre_investigation.py` Dependencies and nodes to use `config.agent_for()`
- [x] Step 2: Update `support_review.py` Dependencies and nodes to use `config.agent_for()`
- [x] Step 3: Update `chart_generation.py` to use `config.agent_for()`
- [x] Step 4: Update `k8s_runner.py` to use `config.agent_for()`
- [x] Step 5: Update callers (worker, chat app, slack handlers)
- [x] Step 6: Update functional tests (conftest, test files)
- [x] Step 7: Update unit tests (error handling, comparison mode)
- [x] Step 8: Remove module-level `agent = build_agent()` from all 8 agent modules (Step 29)
- [x] Step 9: Remove `append_skills_to_prompt` / `render_skills_section` / `SYSTEM_PROMPT` dead code (Step 30)
- [x] Step 10: Run tests and lint, fix any issues

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| ... | ... | ... |

## Outcome

### What was delivered
- All graph pipelines (`sre_investigation`, `support_review`, `chart_generation`) read agents via `agent_for("name")` callable instead of module-level singletons
- Module-level `agent = build_agent()` fallback removed from all 8 agent modules
- `append_skills_to_prompt` removed, replaced by config-driven `compose_system_prompt`
- Functional tests mock via `_build_fake_config()` + `cfg.agent_for` — no direct agent module patching
- Dead `patch_*` backward-compatibility fixtures removed from test conftest files
- `chart_parser_model`/`chart_generator_model` string params removed from `generate_chart()` — model names now extracted from pre-built agents via `agent.model.model_name`

### Follow-up / tech debt
- `render_skills_section` retained in `utils.py` — serves dynamic runtime skill injection (not dead code)
- Agent module imports remain in graph files for `Dependencies` type access (e.g., `alert_classifier.Dependencies(...)`) — this is correct and expected
- `ChartGenerationReply.parser_model`/`.generator_model` fields default to `""` in tests where agents use `"test"` model — production populates real model names via `config.load_agents()`
- Full model-per-call audit trail deferred to prompt-versioning slice (PRD Section 4/6)
