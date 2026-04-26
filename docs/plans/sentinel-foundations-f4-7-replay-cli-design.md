# Design: F4.7 — Replay CLI on RFC §3.8 ReplayBundle

**Status:** spec
**Created:** 2026-04-26
**Parent plan:** [sentinel-foundations-f4-replay-bundle.md](sentinel-foundations-f4-replay-bundle.md)

## Goal

Wire the F4.5/F4.6 capture machinery end-to-end and reroute the replay CLI to use it. After F4.7, every pipeline run persists a canonical RFC §3.8 ReplayBundle (envelope + alert payload + tool I/O + LLM I/O + final outputs), and `python -m sentinel.replay <run_id> --replay` re-executes the run by injecting the recorded I/O — no live LLM, no live tools — yielding bit-for-bit identical output. `--diff` exits non-zero on any drift.

## Non-Goals

- F4.8 (30-run determinism integration test) — separate slice.
- F4.9 (architecture docs + PRD ticks) — separate slice.
- Retiring legacy `pipeline_types.ReplayBundle` and the `fetch_replay_bundle` query — kept side-by-side per parent plan.
- Tolerating tool/LLM call reordering during replay — strict order match (per design decision below).

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Recorded substitution semantics | Strict order + name + input match | Determinism violations should be loud — that is the entire point of the F4.8 follow-up. Silent reordering tolerance defeats the value. |
| Persistence shape | New JSONB column `replay_bundle_json` + text column `replay_bundle_sha` on `pipeline_runs` | Bundle is 1:1 with run, never queried independently, same lifecycle. A separate table would add a join for no benefit. |
| `RecordedModel` scope | Single global queue, ordered | Matches how `llm_io` is recorded (single global builder, append in invocation order). Strict mismatch assertion catches drift; per-agent isolation would add complexity for a guarantee we already get from order. |
| LLM capture mechanism | `WrapperModel` subclass | `event_stream_handler` only fires on streaming runs; our `agent.run()` paths are non-streaming. `WrapperModel` wraps the underlying `Model` instance, captures `request()` / `request_stream()` calls uniformly, requires zero call-site changes. |
| CLI no-flag default | Print new bundle (canonical JSON + sha) | Legacy bundle is being retired. Inspection paths are not scripted. |
| Replay envelope | Use `bundle.envelope` directly | Bit-for-bit replay requires the same `request_id` / `tenant_id` etc. as the original run. The placeholder fresh-UUID envelope in today's CLI was a pre-F4.5 stopgap. |

## Architecture

### Components

#### 1. LLM capture wrapper — `plugins/models/capturing.py` (new)

```python
class CapturingModel(WrapperModel):
    """WrapperModel that records every model request into the active replay builder."""
```

- Subclasses `pydantic_ai.models.WrapperModel`.
- Constructor: `CapturingModel(wrapped: Model, *, agent_name: str)`.
- Overrides `request(messages, model_settings, model_request_parameters)`:
  1. Captures `messages` (serialised via `pydantic_ai`'s message serialiser) as `inputs`.
  2. Delegates to `super().request(...)` to get the real `ModelResponse`.
  3. Captures `response` (serialised) as `outputs`, `usage` as `token_usage`.
  4. Calls `record_llm_call(agent_name=..., model_id=self.wrapped.model_name, inputs=..., outputs=..., token_usage=...)`.
- Overrides `request_stream(...)`: same, but materialises the stream first then records.
- No-op when no replay builder is bound (the `record_llm_call` helper already handles this).

#### 2. Agent factory wraps with `CapturingModel` — `config.py::agent_for`

`agent_for` (on `CommonConfiguration`) is the single indirection point every node uses to obtain an Agent. Wrap the constructed `Model` instance in `CapturingModel(wrapped=real_model, agent_name=registered_name)` before handing it to the `Agent`. One touchpoint, every agent capture-instrumented uniformly. The agent factories themselves (`build_agent`) stay unchanged — capture is a config-layer concern, not an agent-definition concern.

#### 3. Persistence — `data/sql/tracing.py` + Alembic migration

Add to `PipelineRunRecord`:

```python
replay_bundle_json: dict[str, Any] | None = Field(
    default=None, sa_column=Column(JSONB, nullable=True)
)
replay_bundle_sha: str | None = Field(
    default=None, max_length=64, sa_column=Column(Text, nullable=True, index=True)
)
```

Migration: nullable columns, no backfill (rolling-deploy safe).

#### 4. Tracer integration — `domain/pipeline/tracer.py`

`ExecutionTracer` gains:

- `start_pipeline(*, pipeline_type, input_data, envelope=None, alert_payload=None)` — when `envelope` is provided AND `cfg.enable_replay_bundle` is true, instantiate a `ReplayBundleBuilder`, call `bind_replay_builder(builder)`, store the returned `Token` and the `envelope` / `alert_payload` on the tracer instance.
- `complete_pipeline(*, status, output_data, final_reply, runbook_id=None, runbook_version_sha=None)` — calls `flush_replay_capture(token=self._token, envelope=self._envelope, alert_payload=self._alert_payload, runbook_id=..., runbook_version_sha=..., final_outputs=final_reply)`. If a `ReplayBundle` is returned, serialise via `to_canonical_json`, compute sha (use `bundle.bundle_sha`), persist both columns to the run row.
- Failure path: `fail_pipeline()` must also call `flush_replay_capture` (with whatever final outputs are available, even if partial) to release the `ContextVar` token. Capture-on-failure is best-effort and does not block the failure code path.

#### 5. Pipeline integration — `interfaces/graphs/investigation.py`, `interfaces/graphs/support_review.py`

`investigate_alert` / `review_ticket` already build the alert/ticket dict and accept the envelope. Pass both into `et.start_pipeline(..., envelope=envelope, alert_payload=alert.model_dump())` so the tracer can bind. Pass `runbook_id` / `runbook_version_sha` (already known from the runbook resolution step) into `et.complete_pipeline(...)`. No node logic changes.

#### 6. New fetcher — `domain/pipeline/queries.py`

```python
async def fetch_recorded_replay_bundle(
    *,
    db: databases.Database,
    run_id: uuid.UUID,
) -> replay_bundle.ReplayBundle:
```

- Loads `replay_bundle_json` and `replay_bundle_sha` from the row.
- Raises `ReplayBundleNotFoundError(run_id)` when row is missing OR `replay_bundle_json` is null (pre-F4.7 row — caller should know to fall back to legacy if they want).
- Reconstructs `Envelope`, `tuple[ToolIOEntry, ...]`, `tuple[LLMIOEntry, ...]`, `ReplayBundle` from the dict.
- Recomputes `bundle.bundle_sha` and asserts equality with the stored `replay_bundle_sha`. Raises `ReplayBundleSHAMismatchError(run_id, stored, recomputed)` on mismatch — surfaces canonicalisation regressions and DB corruption.

#### 7. RecordedToolset — `plugins/toolsets/recorded.py` (new)

```python
class RecordedToolset(AbstractToolset[Any]):
    """AbstractToolset that returns recorded tool outputs in invocation order."""

    def __init__(self, entries: Sequence[ToolIOEntry]) -> None:
        self._iter: Iterator[ToolIOEntry] = iter(entries)

    async def call_tool(self, name, args, ctx, tool):
        try:
            entry = next(self._iter)
        except StopIteration:
            raise RecordedReplayMismatchError(kind="tool", reason="exhausted", expected=name, actual=None)
        if entry.tool_name != name:
            raise RecordedReplayMismatchError(kind="tool_name", expected=entry.tool_name, actual=name)
        if entry.inputs != dict(args):
            raise RecordedReplayMismatchError(kind="tool_args", expected=entry.inputs, actual=dict(args))
        return entry.outputs
```

- Single instance shared across all toolset slots — replay CLI replaces every `cfg.build_*_toolset()` slot with this single ordered queue (because the original `ReplayBundleBuilder` recorded all tools into one global timeline regardless of which toolset they came from).
- Implements `AbstractToolset` API just enough for `call_tool` — tool listing/discovery delegated to a passthrough or no-op (TBD during impl, depending on what the pipeline calls — almost certainly we never need `list_tools` during replay because the pipeline only calls `call_tool`).

#### 8. RecordedModel — `plugins/models/recorded.py` (new)

```python
class RecordedModel(WrapperModel):
    """WrapperModel that returns recorded LLM outputs in invocation order."""

    def __init__(self, entries: Sequence[LLMIOEntry]) -> None:
        super().__init__(_NoOpModel())  # never delegated to
        self._iter: Iterator[LLMIOEntry] = iter(entries)

    async def request(self, messages, model_settings, model_request_parameters):
        try:
            entry = next(self._iter)
        except StopIteration:
            raise RecordedReplayMismatchError(kind="llm", reason="exhausted", expected=None, actual=None)
        # Optional: assert agent_name match by stashing it on RunContext. For this slice, order alone is enough.
        return _reconstruct_model_response(entry.outputs)
```

- `_reconstruct_model_response` rebuilds a `ModelResponse` from the canonicalised dict so PydanticAI's structured-output coercion produces an identical Python value. Use PydanticAI's own `ModelResponse` Pydantic model + `model_validate` for round-trip fidelity.
- `request_stream` raises `NotImplementedError` — replay does not support streaming runs in this slice.

#### 9. CLI rewire — `src/sentinel/replay.py`

- `--replay` / `--diff` paths:
  1. Load via `fetch_recorded_replay_bundle` (sha-verified).
  2. Build the single `RecordedToolset` queue from `bundle.tool_io`, single `RecordedModel` queue from `bundle.llm_io`.
  3. Wrap `cfg.agent_for` so every returned agent has its `model` replaced with the shared `RecordedModel`.
  4. Wrap `cfg.build_*_adapter()` / `cfg.build_*_toolset()` to inject the shared `RecordedToolset` (or to no-op the ones whose tools went through the recorded path).
  5. Pass `bundle.envelope` and `alert_entities.Alert.model_validate(bundle.alert_payload)` into `investigate_alert` (or the support equivalent).
  6. After the pipeline returns, diff `result.model_dump()` against `bundle.final_outputs`. Exit 3 on drift, exit 0 on match.
- No-flag path: print the canonical JSON of the new bundle plus its sha. Drop legacy print.

### New errors — `domain/pipeline/errors.py`

```python
class ReplayBundleSHAMismatchError(Exception):
    def __init__(self, run_id, stored_sha, recomputed_sha): ...

class RecordedReplayMismatchError(Exception):
    def __init__(self, *, kind, expected, actual, reason=None): ...
```

### Data flow

#### Recording (live run)

```
investigate_alert(alert, envelope, ...)
  → et.start_pipeline(envelope=..., alert_payload=...)
      → bind_replay_builder(builder)  # ContextVar token stored on tracer
  → graph runs
      → ReplayCapturingToolset.call_tool(...)
          → record_tool_call(...)  # appends ToolIOEntry
      → CapturingModel.request(...)
          → record_llm_call(...)  # appends LLMIOEntry
  → et.complete_pipeline(final_reply=..., runbook_id=..., runbook_version_sha=...)
      → flush_replay_capture(...)  # builds ReplayBundle, releases ContextVar
      → to_canonical_json(bundle), bundle.bundle_sha
      → UPDATE pipeline_runs SET replay_bundle_json=..., replay_bundle_sha=...
```

#### Replay (CLI)

```
python -m sentinel.replay <run_id> --diff
  → fetch_recorded_replay_bundle(run_id)
      → recompute sha, assert == stored sha
  → recorded_toolset = RecordedToolset(bundle.tool_io)
  → recorded_model = RecordedModel(bundle.llm_io)
  → wrap cfg.agent_for with model=recorded_model
  → wrap cfg.build_*_toolset with recorded_toolset
  → investigate_alert(Alert(**bundle.alert_payload), envelope=bundle.envelope, ...)
      → graph runs against recorded substitutes
      → recorded entries pop in order; mismatches raise
  → diff result.model_dump() vs bundle.final_outputs
      → exit 3 on drift, exit 0 on match
```

## Test Plan

| Test file | What it covers |
|-----------|----------------|
| `tests/unit/utils/test_replay_bundle_persistence.py` | Bundle → canonical JSON → reconstructed bundle → sha matches; field drift breaks sha |
| `tests/unit/plugins/models/test_capturing_model.py` | Wraps a stub `Model`; on `request` records one `LLMIOEntry` with correct agent_name / model_id / inputs / outputs / usage |
| `tests/unit/plugins/models/test_recorded_model.py` | Pops entries in order; raises `RecordedReplayMismatchError` on exhaustion; reconstructs `ModelResponse` round-trip |
| `tests/unit/plugins/toolsets/test_recorded_toolset.py` | Ordered pop; name mismatch raises; args mismatch raises; exhaustion raises |
| `tests/unit/domain/pipeline/test_recorded_replay_query.py` | Fetcher reconstructs bundle from JSONB; sha mismatch raises; null bundle raises NotFound |
| `tests/unit/domain/pipeline/test_tracer_replay_persistence.py` | `complete_pipeline` writes `replay_bundle_json` and `replay_bundle_sha` columns when a builder was bound |
| `tests/integration/test_replay_pipeline.py` | Run the SRE pipeline against stub agents/toolsets, persist bundle, replay, assert outputs match |

(F4.8's 30-run determinism integration test lands in the next slice.)

## Migration

One Alembic migration adds two nullable columns to `pipeline_runs`:

- `replay_bundle_json JSONB NULL`
- `replay_bundle_sha VARCHAR(64) NULL` (with B-tree index)

Pre-F4.7 rows have NULL in both. The new fetcher raises `ReplayBundleNotFoundError` on NULL — callers fall back to the legacy fetcher (or surface "no bundle available" to the user).

## Open Items (for impl)

- Exact `_reconstruct_model_response` shape — depends on PydanticAI's `ModelResponse` serialisation surface; verify during impl that `ModelResponse.model_validate(serialised)` round-trips losslessly across the message types we actually use (text, tool_call, tool_return).
- `RecordedToolset` `list_tools` behaviour — likely no-op or delegate to a `FunctionToolset` skeleton; confirm by running the SRE pipeline against a recorded toolset and seeing what method PydanticAI actually calls.
- Whether the wrapped `cfg.build_*_toolset` injection point is one place or several — if PydanticAI consults toolsets at agent-construction time vs at run time, the wrapping point shifts. Verify against the existing toolset wiring during impl.

## Outcome

_Fill in after completion._
