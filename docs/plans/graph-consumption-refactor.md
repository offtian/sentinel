# Plan: Graph Consumption Refactor

**Status:** in-progress
**Created:** 2026-04-09
**Last updated:** 2026-04-09

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

- [ ] Step 1: Update `sre_investigation.py` Dependencies and nodes to use `config.agent_for()`
- [ ] Step 2: Update `support_review.py` Dependencies and nodes to use `config.agent_for()`
- [ ] Step 3: Update `chart_generation.py` to use `config.agent_for()`
- [ ] Step 4: Update `k8s_runner.py` to use `config.agent_for()`
- [ ] Step 5: Update callers (worker, chat app, slack handlers)
- [ ] Step 6: Update functional tests (conftest, test files)
- [ ] Step 7: Update unit tests (error handling, comparison mode)
- [ ] Step 8: Remove module-level `agent = build_agent()` from all 8 agent modules (Step 29)
- [ ] Step 9: Remove `append_skills_to_prompt` / `render_skills_section` / `SYSTEM_PROMPT` dead code (Step 30)
- [ ] Step 10: Run tests and lint, fix any issues

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| ... | ... | ... |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
