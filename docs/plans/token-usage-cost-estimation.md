# Plan: Token Usage Cost Estimation

**Status:** in-progress
**Created:** 2026-04-12
**Last updated:** 2026-04-12

## Goal

Track the USD cost of LLM calls per investigation run so operators can compare cost across backends and monitor spend. The existing `token_cost: int` field tracks total token count; `token_cost_usd: float` adds the monetary dimension.

## Scope

### In scope
- Add `token_cost_usd: float` field to `EvaluationMetrics`
- Add `token_cost_usd` to `_LOWER_IS_BETTER` in comparison
- Create cost estimation helper
- Wire cost tracking into `ExecutionTracer` and graph nodes
- Update functional test helpers

### Out of scope
- Per-model pricing configuration UI
- Billing integration

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Separate field from token_count | `token_cost_usd: float` alongside `token_cost: int` | Token count and USD cost are distinct concepts; keeps existing callers unbroken |
| Default value | `0.0` | Non-breaking addition — existing code creating `EvaluationMetrics` without the field still works |
| Lower is better | Yes | Lower USD spend is preferable when comparing backends |

## Steps

- [ ] Step 1: Add `token_cost_usd: float = 0.0` to `EvaluationMetrics` and `"token_cost_usd"` to `_LOWER_IS_BETTER`
- [ ] Step 2: Create cost estimation helper and unit tests
- [ ] Step 3: Add `record_agent_result` to `ExecutionTracer` and tests
- [ ] Step 4: Update `FakeAgentResult` in functional conftest
- [ ] Step 5: Wire `record_agent_result` into graph nodes

## Changes

| Date | What changed | Why |
|------|-------------|-----|

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
