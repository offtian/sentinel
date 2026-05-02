# Plan: F8 CI fix — migrate request_id propagation test to LangGraph SRE API

**Status:** in-progress
**Created:** 2026-05-02
**Last updated:** 2026-05-02

## Goal

Fix the CI `ImportError` in `tests/integration/interfaces/api/test_request_id_propagation.py`
caused by the same archived-module import pattern fixed in `test_replay_determinism.py`.
The test imports `sentinel.interfaces.graphs.investigation` (archived in PR #35) and
calls the old Pydantic Graph `investigate_alert(agent_for=, post_to_slack=)` signature.

## Scope

### In scope
- Update `test_request_id_propagation.py` to use `sre_mod.investigate_alert(alert=, envelope=, graph=)` with `mock.patch.object(sre_mod, "get_config", ...)` for the SRE pipeline calls
- Keep the support_review calls unchanged (still active Pydantic Graph)

### Out of scope
- Any other test files
- Functional changes to production code

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| LangGraph API pattern | `build_sre_investigation_graph(MemorySaver())` + `patch get_config` | Same pattern used in `test_replay_determinism.py` — consistent, no DB needed |
| Config mock | Extend `_build_fake_config()` result with LangGraph-required fields | `_build_fake_config` returns MagicMock; setting `post_to_slack=False`, `db_session_factory=None`, etc. prevents side effects |

## Steps

- [x] Update imports: remove `investigation`, add `sre_mod` + `MemorySaver`
- [x] Update `patched_sre_router` fixture's `fake_enqueue` to use LangGraph API
- [x] Update inline `fake_enqueue` in `test_redacted_pii_class_emits_tenant_hash_instead_of_tenant_id`
- [x] Verify `just lint` and `just test` pass

## Outcome

_Fill in after completion._
