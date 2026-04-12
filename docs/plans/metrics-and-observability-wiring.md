# Plan: Metrics & Observability Wiring

**Status:** draft
**Created:** 2026-04-12
**Last updated:** 2026-04-12

## Goal

Wire up the existing metrics infrastructure that's been declared but not connected. Sentinel has OTel instruments, DB schema, and tracer methods ready — but many are never called from pipeline code. This plan closes the gap between "infrastructure exists" and "data flows end-to-end". Spins up AI Engineer and Data Engineer for these metrics. Metrics should be stored in the DB with Alembic migration. 

## Scope

### In scope

**Online metrics (user-facing signals):**
- SRE approval persistence (replace in-memory dict with DB)
- SRE investigation stats endpoint (parity with `/api/support/stats`)
- Quality verdict persistence on investigation/review records

**Offline metrics (operational):**
- Token usage extraction from `AgentRunResult.usage()` into `agent_calls.token_usage_json`
- LLM cost estimation (model pricing table + cost calculation)
- Wire `record_llm_call()` into pipeline nodes (currently declared, never called)
- Wire `record_approval_decision()` into approval handlers (currently declared, never called)

### Out of scope
- TTFT (time to first token) — requires PydanticAI streaming event handler; defer until PydanticAI exposes this natively
- Time-series feedback trends / dashboards — rely on Prometheus/Grafana queries over `/metrics` endpoint
- Eval framework maturity (separate plan)
- LiteLLM deployment mode change (separate plan)

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Token extraction location | Graph node wrappers (`_node_helpers.py`) | Captures usage for every agent call uniformly; avoids duplicating extraction in each node |
| Cost estimation approach | Static pricing table in `domain/evaluation/costing.py` | Gateway-independent; LiteLLM `response_cost` not reliably available for all providers |
| SRE approval storage | New `approval_records` table | Separate from `investigation_records` — approvals have their own lifecycle (pending → approved/rejected) |
| Quality verdict storage | JSONB column on existing records | Lightweight; avoid new table for a field that's always 1:1 with the parent record |

## Steps

### Phase 1: Token Usage & LLM Metrics (offline metrics)

- [ ] Step 1: Create `_extract_agent_usage()` helper in `_node_helpers.py` that calls `result.usage()` and returns a dict compatible with `token_usage_json`
- [ ] Step 2: Wire token extraction into `instrumented_node_run()` wrapper so every agent call records usage via `tracer.record_agent_call(token_usage=...)`
- [ ] Step 3: Wire `metrics.record_llm_call()` into the same wrapper — call count + duration histogram per agent/model
- [ ] Step 4: Create `domain/evaluation/costing.py` with static pricing table (GPT-4.1, GPT-4.1-mini, Claude models) and `estimate_cost(model, usage)` function
- [ ] Step 5: Add integration test asserting non-null `token_usage_json` on `AgentCallRecord` after a pipeline run

### Phase 2: SRE Approval Persistence (online metrics)

- [ ] Step 6: Create `ApprovalRecord` SQLModel in `data/models.py` with fields: `investigation_id`, `alert_id`, `confidence_label`, `confidence_total`, `decision`, `reviewed_by`, `reviewed_at`, `requested_at`, `slack_message_ts`
- [ ] Step 7: Add Alembic migration for `approval_records` table
- [ ] Step 8: Create `domain/approval/operations.py` — `persist_approval()`, `update_approval_decision()`
- [ ] Step 9: Create `domain/approval/queries.py` — `fetch_approval_by_investigation_id()`, `fetch_approval_stats()`
- [ ] Step 10: Replace `_pending_approvals` dict in `sre/router.py` with DB-backed operations
- [ ] Step 11: Wire `metrics.record_approval_decision()` into approval endpoint handlers

### Phase 3: Stats & Quality Verdict (online metrics)

- [ ] Step 12: Create `GET /api/sre/stats` endpoint returning investigation counts by outcome, approval rates by decision type, confidence distribution
- [ ] Step 13: Add `quality_verdict_json` JSONB column to `investigation_records` and `ticket_review_records` (Alembic migration)
- [ ] Step 14: Wire quality verdict persistence into supervisor orchestrator after `evaluate_*_quality()` calls
- [ ] Step 15: Add quality verdict data to stats endpoints

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-12 | Initial plan created from research | Identified 7 unwired metrics instruments + SRE approval volatility |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- TTFT tracking (blocked on PydanticAI streaming support)
- Time-series trend queries (defer to Grafana dashboards)
- Skill activation audit persistence (separate from this plan — tracked in OTel telemetry plan)
