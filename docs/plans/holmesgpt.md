# Plan: HolmesGPT Phase E — Enhance Investigation Engine

**Status:** complete
**Created:** 2026-04-10
**Last updated:** 2026-04-10

## Goal

Resolve the HolmesGPT integration strategy and enhance the investigation engine. The pydantic-ai >=1.0.7 dependency conflict blocks the HolmesGPT SDK. DirectToolsetAdapter is the working production implementation (14 tests, circuit breaker, concurrent queries). Phase E formalizes this adapter, adds Kubernetes state queries, and wires the K8s investigation backends into the Holmes flow.

Also: check off the "dynamic skill selection" PRD item which is already fully implemented but unchecked.

## Scope

### In scope
- Check upstream HolmesGPT compatibility with pydantic-ai>=1.0.7, think of an alternative if this doesn't work
- Formalize DirectToolsetAdapter as the primary investigation engine
- Add Kubernetes cluster state queries (pod status, events, resource usage) to DirectToolsetAdapter
- Wire existing K8s backends (NativeK8sAgent, KagentAdapter) into the investigation flow
- Implement comparison framework (challenger adapter scoring)
- Update PRD acceptance criteria (dynamic skill selection is done)

### Out of scope
- Hybrid documentation search (BM25 + embeddings) — separate plan
- Prompt caching (Phase D)
- Production deployment and benchmarking (post-deploy)

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| SDK vs DirectToolsetAdapter | Both — DirectToolsetAdapter as default, HolmesAdapter as opt-in | Resolved dep conflict via fork (offtian/holmesgpt@httpx-compat); both adapters return same HolmesInvestigationResult |
| Dependency resolution | Fork with relaxed httpx/postgrest pins | Upstream pins httpx<0.28 and postgrest==0.16.8; our fork relaxes to httpx<1.0 and postgrest>=2.0 |
| K8s data source | Add K8s queries to DirectToolsetAdapter via K8sClient | Reuses existing K8s tooling; HolmesAdapter gets K8s via SDK's KubernetesToolset |
| Comparison framework | Use existing challenger_adapter infrastructure in InvestigateWithHolmes node | Scaffolding already exists at sre_investigation.py:154-185 |

## Steps

_Pending user review — research phase complete. See thoughts/research/2026-04-10_phase-e-holmesgpt.md for full analysis._

- [x] Step 1: Check upstream HolmesGPT PyPI for pydantic-ai compatibility — httpx conflict confirmed, upstream incompatible
- [x] Step 2: Update PRD — check off dynamic skill selection (already implemented)
- [x] Step 3: Add K8s state query methods to BaseObservabilityClient interface
- [x] Step 4: Implement K8s queries in DirectToolsetAdapter (pod status, events, resource usage)
- [x] Step 5: Wire K8s investigation backends into InvestigateWithHolmes node
- [x] Step 6: Implement comparison scoring (EvaluationMetrics) for challenger adapter
- [x] Step 7: Add unit tests for K8s queries in DirectToolsetAdapter
- [x] Step 8: Add functional tests for comparison mode pipeline
- [x] Step 9: Update architecture docs and adapter hierarchy diagram
- [x] Step 10: Resolve dependency conflict via fork (offtian/holmesgpt@httpx-compat)
- [x] Step 11: Implement real HolmesGPT SDK integration in HolmesAdapter
- [x] Step 12: Wire HolmesAdapter into config as opt-in alternative (HOLMES_BACKEND=sdk)
- [x] Step 13: Unit tests for HolmesAdapter SDK integration

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-10 | Initial research completed | Dynamic skill selection found to be already done; refocused scope |
| 2026-04-10 | Dependency conflict resolved | Forked holmesgpt with relaxed httpx (<1.0) and postgrest (>=2.0) pins; SDK now installable alongside pydantic-ai>=1.0.7 |
| 2026-04-10 | Scope expanded: real SDK integration | With dependency resolved, implementing real HolmesAdapter using ToolCallingLLM; DirectToolsetAdapter stays default |

## Outcome

_Fill in after completion._

### What was delivered
- HolmesGPT SDK installed via fork (offtian/holmesgpt@httpx-compat) resolving httpx/postgrest conflicts
- Real HolmesAdapter implementation using ToolCallingLLM with asyncio.to_thread()
- DirectToolsetAdapter enhanced with K8s state queries (pod status, events, logs)
- K8s adapter wired into InvestigateWithHolmes pipeline node
- Comparison framework configurable via CHALLENGER_ADAPTER setting
- HOLMES_BACKEND setting to switch between "direct" (default) and "sdk" backends
- PRD dynamic skill selection checkbox corrected, HolmesGPT gap resolved
- 569 tests passing, lint clean, all import-linter contracts kept

### Follow-up / tech debt
- Track upstream robusta-dev/holmesgpt for httpx/postgrest pin updates — switch from fork to PyPI when merged
- Validate HolmesAdapter SDK against real alerts (only tested with mocks so far)
- Consider making HolmesGPT SDK the default once validated in production
