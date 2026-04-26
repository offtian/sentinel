# Plan: Sentinel Foundations F4 Phase B — ReplayBundle + determinism

**Status:** complete
**Created:** 2026-04-26
**Last updated:** 2026-04-26

## Goal

Land Phase B of foundations Phase F4 (RFC §13 + §3.8 + R-AG-4): the RFC §3.8 ReplayBundle shape, tool/LLM I/O capture, replay CLI on the new shape, 30-run determinism CI, and architecture docs. Phase A (mandatory OTel attrs, Langfuse OTLP exporter, local Langfuse v3 stack) merged via PR #28; this slice closes the §14.7 failure mode by making replays bit-for-bit reproducible.

## Scope

### In scope
- F4.5 — `src/sentinel/utils/replay_bundle.py`: `ReplayBundle` `attrs.frozen` per RFC §3.8 with `envelope`, `alert_payload`, `runbook_id`, `runbook_version_sha`, `tool_io`, `llm_io`, `final_outputs`, `bundle_sha`. `ToolIOEntry` and `LLMIOEntry` companion frozen attrs.
- F4.6 — Tool I/O capture in `plugins/toolsets/_runtime.py` via `ContextVar[ReplayBundleBuilder]`; flush on pipeline `End`; per-toolset wrapper registration.
- F4.7 — Reroute `python -m sentinel.replay <run_id>` (existing `src/sentinel/replay.py`) to consume the new ReplayBundle; `RecordedTransport` injects recorded LLM/tool outputs; `--diff` exits non-zero on drift.
- F4.8 — `tests/integration/test_replay_determinism.py`: 30-run identical-output assertion for a synthetic crashloop bundle. Marked `slow`. Wired into `just test-integration`.
- F4.9 — `docs/architecture.md` §Observability mandatory-attribute table and §Replay subsection; PRD checkbox updates for R-OB-2 / R-AG-4.
- Compose hygiene: split the Grafana stack (prometheus/loki/tempo/grafana) behind a `--profile grafana` so default `docker compose up` only pulls the Langfuse + app slice.

### Out of scope
- F4.4 runtime smoke (Docker pull blocked on this host; tracked in foundations plan as deferred until dev host has Docker).
- 100-run nightly determinism (foundations does 30; nightly expansion lives in week 5 plan).
- LiteLLM proxy migration / orchestration framework re-eval (Phase F5).

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Existing `domain/pipeline/types.py::ReplayBundle` schema vs RFC §3.8 schema | New module `utils/replay_bundle.py`; legacy stays for current queries | Avoids breaking existing `fetch_replay_bundle` callers in this slice. F4.7 reroutes the CLI; legacy can be retired in a follow-up once persistence write path is dual-tracked. |
| CLI module path | Keep `python -m sentinel.replay` (extend existing `src/sentinel/replay.py`) | Plan filemap calls for `replay_cli.py` but the documented UX already lives at `sentinel.replay`. Renaming would break docs and muscle memory for no payoff. |
| Determinism scope | Synthetic crashloop bundle programmatically constructed | No live alert path required; CI reproducibility doesn't depend on fixture recording infrastructure. |
| Grafana stack | Behind `profiles: ["grafana"]`; default compose pulls Langfuse + app only | Cuts ~4 images from default `docker compose up`; Langfuse is the F4 target, Tempo/Prometheus/Loki/Grafana are the orthogonal Grafana path. |

## Steps

- [x] Edit `docker-compose.yml` to add `profiles: ["grafana"]` to prometheus, loki, tempo, grafana
- [x] F4.5: Implement `src/sentinel/utils/replay_bundle.py` with `ReplayBundle`, `ToolIOEntry`, `LLMIOEntry` frozen attrs + bundle_sha computation
- [x] F4.5: Unit tests `tests/unit/utils/test_replay_bundle.py` — bundle_sha determinism, field-change detection
- [x] F4.6: Implement `src/sentinel/plugins/toolsets/_runtime.py` with `ContextVar[ReplayBundleBuilder]` + tool wrapper
- [x] F4.6: Wire flush into pipeline tracer's `complete_pipeline()` path (F4.7 slice A)
- [x] F4.6: Update `plugins/toolsets/observability.py`, `documentation.py`, `mcp.py` to register through the wrapper
- [x] F4.6: Unit tests covering accumulation order, ContextVar isolation under asyncio.gather, flush serialisation
- [x] F4.7: Reroute `src/sentinel/replay.py` to read `utils.replay_bundle.ReplayBundle`; inject `RecordedModel` + `RecordedToolset` for LLM + tool calls (slice D, commit `318d887`)
- [x] F4.7: Persistence — write the new bundle alongside the legacy bundle (Alembic 014; tracer integration in slice A, commit `6a4995d`)
- [x] F4.8: Integration test `tests/integration/test_replay_determinism.py` — 30 identical replays, `@pytest.mark.slow`
- [x] F4.8: Verify `just test-integration` picks up the new file (default `tests/integration/` glob; `slow` marker registered in `pyproject.toml`)
- [x] F4.9: Update `docs/architecture.md` §Observability mandatory-attribute table + §Replay subsection
- [x] F4.9: Tick F4.5–F4.9 in `docs/plans/sentinel-hedgefund-foundations.md`
- [x] F4.9: Update PRD R-OB-2 and R-AG-4 checkboxes — F4.7/F4.8 line items already ticked under §4 of `docs/prd.md` (replay re-execution + 30-run determinism)

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-26 | Plan drafted; branched off main (PR #28 merged) | Phase B kickoff; Phase A landed via PR #28 (4 commits — F4.1, F4.A.1, F4.2, F4.3 + docs). |
| 2026-04-26 | Phase B complete — F4.5–F4.9 all merged onto `feat/sentinel-foundations-f4-replay-bundle`; ready for PR review | All ten plan steps ticked; replay determinism CI green (30× SRE graph in 2.4s); `docs/architecture.md` gains §Replay subsection. |

## Outcome

### What was delivered
- F4.5: `utils/replay_bundle.py` — RFC §3.8 frozen bundle with `ToolIOEntry` / `LLMIOEntry` companions and canonical `bundle_sha`.
- F4.6: `plugins/toolsets/_runtime.py` — `ReplayCapturingToolset` + `ContextVar`-based builder binding for tool I/O capture.
- F4.7 slice A: persistence backbone — `replay_bundle_json` + `replay_bundle_sha` columns on `pipeline_runs` (Alembic 014), tracer integration, worker + graph wiring.
- F4.7 slice B: `plugins/models/capturing.py` — `CapturingModel` (`WrapperModel` subclass) wraps every agent's `.model` in `load_agents()` so `agent.run()` calls record into the bound builder.
- F4.7 slice C: replay primitives — `RecordedModel`, `RecordedToolset`, sha-verified `fetch_recorded_replay_bundle()`, new errors (`ReplayBundleSHAMismatchError`, `RecordedReplayMismatchError`).
- F4.7 slice D: `src/sentinel/replay.py` rewired onto the new bundle; `--diff` exits 3 on drift, 4 on SHA mismatch, 5 on tool/LLM call drift.
- F4.8: `tests/integration/test_replay_determinism.py` — 30-run identical-output assertion against the SRE graph + canonicalisation-drift companion test, both marked `slow`.
- F4.9: `docs/architecture.md` §Replay subsection (bundle shape, capture flow, replay flow, determinism guarantee, exit codes).

### Follow-up / tech debt
- Retire legacy `domain/pipeline/types.py::ReplayBundle` once all readers move to `utils.replay_bundle.ReplayBundle`.
- Expand 30-run determinism CI to 100-run nightly once Helm lands (week 5 plan).
- F4.4 runtime smoke screenshot for Langfuse trace tree (deferred until Docker on dev host).
- Lift `bind_replay_builder` / `flush_replay_capture` from `plugins.toolsets._runtime` to `sentinel.utils.replay_runtime` to remove the layer-rule exception added in `pyproject.toml`.
- F6 wires real `runbook_id` / `runbook_version_sha` into the SRE pipeline; today both fields are `None` everywhere.

### Follow-up / tech debt
- Retire legacy `domain/pipeline/types.py::ReplayBundle` once all readers move to `utils.replay_bundle.ReplayBundle`.
- Expand 30-run determinism CI to 100-run nightly once Helm lands (week 5 plan).
- F4.4 runtime smoke screenshot for Langfuse trace tree (deferred until Docker on dev host).
