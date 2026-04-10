# Plan: Migrate LLM Settings to Configuration Layer

**Status:** draft
**Created:** 2026-04-10
**Last updated:** 2026-04-10

## Goal

Move LLM model name normalisation and per-agent model properties out of
`BaseConfiguration` and into `CommonConfiguration`, keeping the base
config layer free of LLM-specific concerns.

Currently `BaseConfiguration` (in `sentinel/config.py`) owns:

- `_normalise_model_name()` — converts `openai/gpt-4.1-mini` to
  `openai:gpt-4.1-mini` for pydantic-ai.
- Eight `@property` accessors (`classifier_model`, `analyser_model`,
  `reviewer_model`, `drafter_model`, `k8s_investigator_model`,
  `intent_router_model`, `chart_parser_model`, `chart_generator_model`)
  that read from `Settings` and normalise.
- `chart_max_retries` property.

These belong in the plugins/config layer where agents are actually built,
not in the lightweight base that every layer depends on.

## Scope

### In scope

- Move `_normalise_model_name` to `CommonConfiguration` (or a shared
  utility in `plugins/`)
- Move all `*_model` properties and `chart_max_retries` to
  `CommonConfiguration`
- Keep stub properties on `BaseConfiguration` only if callers outside
  `plugins` need them (audit first)
- Update `load_agents()` to use `self.classifier_model` etc. directly
  (already does, but verify after move)

### Out of scope

- Changing `Settings` env var names or structure
- Introducing a model registry or dynamic model selection
- Moving vendor adapter wiring (already done)

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Where to put normalisation | `CommonConfiguration` | Only `load_agents()` and builder methods need it — all in `CommonConfiguration` |
| Keep stubs on base? | No — remove entirely | Audit shows no caller outside `plugins` uses the model properties |
| Move `chart_max_retries`? | Yes | It's a pipeline config detail, not base infrastructure |

## Steps

- [ ] Step 1: Audit all callers of `classifier_model`, `analyser_model`, etc. outside `plugins/config.py`
- [ ] Step 2: Move `_normalise_model_name` to `CommonConfiguration`
- [ ] Step 3: Move all `*_model` properties and `chart_max_retries` to `CommonConfiguration`
- [ ] Step 4: Remove the properties and normalisation function from `BaseConfiguration`
- [ ] Step 5: Verify all 532+ tests pass and import-linter is clean
- [ ] Step 6: Update docstrings in `config.py` and `plugins/config.py`

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-10 | Plan created | Identified during config refactoring PR |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
