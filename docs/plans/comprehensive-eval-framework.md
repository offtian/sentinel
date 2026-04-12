# Plan: Comprehensive Agent Evaluation Framework

**Status:** complete
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
- [x] Step 2: Create `evaluators/semantic.py` — LLM-as-judge evaluators
- [x] Step 3: Create `evaluators/safety.py` — hallucination and safety evaluators
- [x] Step 4: Create `metrics.py` — scoring model and metric taxonomy
- [x] Step 5: Add golden datasets for uncovered agents
- [x] Step 6: Extend `cases/base.py` with new agent builders
- [x] Step 7: Update `runner.py` with composite scoring
- [x] Step 8: Extend `reporting.py` and `rendering.py`
- [x] Step 9: Update `evaluators/__init__.py`
- [x] Step 10: Add unit tests for all new code
- [x] Step 11: Add `pydantic-evals` to dev dependencies
- [x] Step 12: Add eval result persistence to database

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-12 | Started implementation | Plan approved by user |
| 2026-04-12 | Added eval result persistence | Moved from out-of-scope to delivered |

## Outcome

### What was delivered

- **Shared evaluator base** — `evaluators/base.py` with `resolve_field()` utility, eliminating 3x duplication across evaluator modules
- **4 LLM-as-judge semantic evaluators** (`evaluators/semantic.py`) — Faithfulness, Relevance, Coherence, Completeness; configurable judge model via `EVAL_JUDGE_LLM` env var (default `openai/gpt-4.1-mini`)
- **3 safety evaluators** (`evaluators/safety.py`) — GenericPhraseCheck (deterministic phrase list ported from quality gate), HallucinationDetector (LLM-based), ToneCheck (LLM-based)
- **Per-agent metric taxonomy** (`metrics.py`) — `AgentMetricSpec` with weighted `MetricWeight` entries per agent; `compute_composite_score()` produces a single 0–1 quality number
- **Golden datasets for all 7 agents** — added `intent_router_cases.json`, `k8s_investigator_cases.json`, `ticket_reviewer_cases.json` (3 new); existing 4 datasets unchanged
- **Extended case builders** (`cases/base.py`) — new builders for intent_router, ticket_reviewer, k8s_investigator wiring semantic + safety evaluators
- **Composite scoring in runner** — `runner.py` computes composite scores per case and attaches to `EvaluationReport`
- **Color-coded report rendering** — `rendering.py` displays composite scores with pass/warn/fail color coding
- **60 new unit tests** across `test_base.py` (5), `test_safety.py` (11), `test_semantic.py` (8), `test_metrics.py` (14), `test_alert_classifier_eval.py` (9), `test_root_cause_eval.py` (13)
- **`pydantic-evals`** added as explicit dev dependency
- **Eval result persistence** — migration 006 extends `eval_runs` with `agent_name`, `composite_score`, `assertion_details_json` columns; runner `persist=True` flag saves per-agent results with graceful fallback when DB is unavailable; 17 additional unit tests covering data model, operations, queries, and runner persistence wiring

### Follow-up / tech debt

- **CI integration** — eval suite runs via `just test-evals` but not wired into GitHub Actions CI pipeline
- **Dashboard** — no visual dashboard for eval trends; consider DeepEval or Braintrust if needed (noted in PRD remaining gaps)
- **Production calibration** — LLM-as-judge thresholds (0.6 default) tuned against synthetic data only; will need adjustment after real incident data is available
