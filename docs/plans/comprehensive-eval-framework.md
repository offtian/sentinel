# Plan: Comprehensive Agent Evaluation Framework

**Status:** in-progress
**Created:** 2026-04-12
**Last updated:** 2026-04-12

## Goal

Expand the eval framework from 2 deterministic evaluators covering 4 agents to a comprehensive suite with LLM-as-judge semantic evaluators, hallucination detection, per-agent metric taxonomy, and composite scoring — covering all 7 agents.

## Scope

### In scope
- Shared evaluator base module (deduplicate `_resolve_field`)
- LLM-as-judge evaluators (faithfulness, relevance, coherence, completeness)
- Safety evaluators (hallucination, generic phrase, tone)
- Per-agent metric weights and composite scoring
- Golden datasets for 3 uncovered agents (intent_router, ticket_reviewer, k8s_investigator)
- Extended runner, reporting, and rendering

### Out of scope
- CI integration changes
- Eval result persistence to database
- Production quality gate changes

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Evaluator base | Shared `resolve_field` in `base.py` | Deduplicate 3x copy |
| LLM judge model | Configurable via `EVAL_JUDGE_LLM` env var | Flexibility for cost/quality tradeoff |
| Scoring model | `attrs.frozen` classes with weighted composite | Immutable, testable, per-agent customizable |
| Safety evaluators | Port from `quality_gate.py` + LLM judge | Reuse existing phrase lists, add semantic checks |

## Steps

- [x] Step 1: Create `evaluators/base.py` + refactor existing evaluators
- [ ] Step 2: Create `evaluators/semantic.py` — LLM-as-judge evaluators
- [ ] Step 3: Create `evaluators/safety.py` — hallucination and safety evaluators
- [ ] Step 4: Create `metrics.py` — scoring model and metric taxonomy
- [ ] Step 5: Add golden datasets for uncovered agents
- [ ] Step 6: Extend `cases/base.py` with new agent builders
- [ ] Step 7: Update `runner.py` with composite scoring
- [ ] Step 8: Extend `reporting.py` and `rendering.py`
- [ ] Step 9: Update `evaluators/__init__.py`
- [ ] Step 10: Add unit tests for all new code
- [ ] Step 11: Add `pydantic-evals` to dev dependencies

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-12 | Started implementation | Plan approved by user |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
