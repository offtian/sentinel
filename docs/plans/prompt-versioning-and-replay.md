# Plan: Prompt Versioning + Replay Snapshots

**Status:** complete
**Created:** 2026-04-08
**Last updated:** 2026-04-12

> **For agentic workers:** REQUIRED SUB-SKILL — use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

## Goal

Make every Sentinel pipeline run fully reproducible from persisted state. After this slice lands, given a `run_id` an operator (or future re-execution harness) can reconstruct the exact prompt text, prompt version/hash, model identifiers, MCP endpoints, skill activations (name + sha256), and input payload that produced a given final reply. **No audit row goes out without `prompt_version` + `prompt_sha256` populated, and no pipeline run is unreplayable.**

Ticks (from `docs/prd.md`):
- §6 "`prompt_version` + `prompt_sha256` captured on every `audit_log` row"
- §6 "skill files content-hashed; skill activations recorded per run"
- §6 "`replay_pipeline_run(run_id)` helper returns the reproducibility bundle"
- §4 "Per-pipeline-run snapshot persisted to `pipeline_runs` / `node_executions` / `agent_calls`" — extended with snapshot columns required for replay

## Scope

### In scope
1. `PromptHandle` frozen attrs class in `plugins/prompts/__init__.py`: `template_name`, `text`, `sha256`, `version`. Computed/cached at module import time.
2. `load_system_prompt(template_name)` refactored to return `PromptHandle` (supersedes the temporary shim introduced by slice 3 at `plugins/prompts/_handle.py`).
3. Git-SHA discovery (`_git_sha()`): precedence = `SENTINEL_GIT_SHA` env → `git rev-parse HEAD` via `subprocess.run(..., timeout=2)` → `"unknown"`. Cached in module-level lru_cache; never re-evaluated.
4. `PromptCacheMissError` raised if a template fails to render the `system` block with an empty context.
5. Extension of the existing `pipeline_runs` table with snapshot columns. **Reuse `tracing_models.PipelineRunRecord`; no parallel table.**
6. `ReplayBundle` frozen attrs class in `domain/pipeline/types.py`.
7. `fetch_replay_bundle(run_id)` in `domain/pipeline/queries.py`.
8. Thread `PipelineRunRecord.id` into every graph's `Dependencies` so each `persist_node_execution` / `record_audit_entry` call carries it.
9. Thread `PromptHandle` (for the top-most agent — classifier / ticket_reviewer) into `Dependencies` so `record_audit_entry` receives `prompt_version` and `prompt_sha256`.
10. Populate snapshot columns at `start_pipeline()`: `input_hash`, `model_ids`, `mcp_endpoints`, `skill_activations`. Populate `final_reply` at `complete_pipeline()`.
11. Alembic migration `004_pipeline_run_snapshot.py` — additive columns only, all nullable.
12. CLI scaffold `src/sentinel/replay.py` — `python -m sentinel.replay <run_id>` loads and pretty-prints the bundle. **Re-execution deferred to slice 6.**
13. Tests — unit, integration (golden round-trip), CLI smoke.

### Out of scope
- Slice 1 (Skills) — `SkillHandle.sha256` consumed here, not produced
- Slice 2 (Universal MCP) — `Configuration.build_mcp_toolsets()` consumed here
- Slice 3 (Anthropic prompt caching) — consumes `PromptHandle.sha256`; this slice produces the contract
- Slice 4 (OTel exporter) — populates audit row fields this slice fills in
- **Actual pipeline re-execution in the CLI** — scaffold only
- Changes to `node_executions`/`agent_calls` schemas — reused as-is
- Prompt-template version history / rollbacks (store a SHA, not a registry)

## Dependency Map

```
          +-------------------+
          | slice 1: Skills   |  produces SkillHandle.sha256
          +---------+---------+
                    |
                    v
+-------------------+--------------------+
| slice 2: Universal MCP                 |  produces Configuration.build_mcp_toolsets()
+-------------------+--------------------+
                    |
                    v
+-------------------+--------------------+
| SLICE 5 (this plan):                   |
|   PromptHandle + PipelineRunRecord     |
|   snapshot fields + fetch_replay_bundle|
|   + replay CLI scaffold                |
+-------+------------------------+-------+
        |                        |
        v                        v
+-------+------------+   +-------+-------------+
| slice 3: prompt    |   | slice 4: OTel       |
| caching            |   | exporter            |
| (consumes .sha256) |   | (consumes version + |
|                    |   |  sha256 on audit)   |
+--------------------+   +---------------------+
```

**Merge order for least friction:** skills (1) → MCP (2) → prompt versioning (5, this plan) → prompt caching (3) → OTel (4). Slice 3 may merge before slice 5 using the `_handle.py` shim; Step 11 deletes the shim.

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Where `PromptHandle` lives | `sentinel.plugins.prompts.__init__` — real, non-shim | Lowest layer that already owns prompt loading |
| `prompt_version` format | `f"{git_sha[:12]}:{template_name}"` | Short, greppable, maps 1:1 to git history; basename avoids path-move breakage |
| `prompt_sha256` scope | SHA-256 of the **rendered** static `system` block (empty context), not the `.j2` source | Matches what the LLM saw; immune to Jinja inheritance quirks |
| Git-SHA precedence | `SENTINEL_GIT_SHA` env > `git rev-parse HEAD` > `"unknown"` | Env var is the Docker-image injection path; subprocess is the dev path; unknown is degraded non-fatal |
| Git-SHA caching | `functools.lru_cache(maxsize=1)` on `_git_sha()` | Never re-shell |
| `PromptHandle` caching | `functools.lru_cache(maxsize=None)` on `load_system_prompt` | Render once per template per process; tests use `cache_clear()` |
| `input_hash` algorithm | `sha256(json.dumps(payload, sort_keys=True, separators=(",",":"), default=str).encode())` | Canonical JSON; deterministic; handles datetimes |
| `input_hash` field selection | Exclude `timestamp`/`received_at`/`now`/`run_id`/`trace_id`/`pipeline_run_id`; include everything else | Two runs of the same alert at different times MUST hash-match |
| Reuse `tracing_models.PipelineRunRecord` vs new table | **Reuse** with additive nullable columns | PRD §4 explicitly says "persisted to `pipeline_runs`"; parallel table would double-write |
| New columns on `pipeline_runs` | `input_hash`, `model_ids_json`, `mcp_endpoints_json`, `skill_activations_json`, `final_reply`, `prompt_version`, `prompt_sha256`, `prompt_text` — all nullable | Exact replay bundle surface; nullable for rolling deploys |
| `prompt_version` on `pipeline_runs` AND `audit_log` | Denormalise on `pipeline_runs` to avoid JOIN on hot path | 64-byte duplication worth it |
| Where `run_id` lives in graph state | `Dependencies`, not `State` | DI surface vs per-run mutable data |
| Replay strategy for prompt text | **Store rendered prompt text** on `pipeline_runs.prompt_text` | Git history isn't guaranteed (squash merges, retention); byte-exact stored text is the only reliable replay source |
| CLI module shape | `src/sentinel/replay.py` with `main()` + `if __name__ == "__main__":`; invoked as `python -m sentinel.replay <run_id>` | Matches existing `sentinel.worker` pattern |
| Alembic migration | Single additive migration `004_pipeline_run_snapshot.py`; no backfill | Back-compat: older rows read `None` |

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `src/sentinel/replay.py` | CLI scaffold: load + pretty-print `ReplayBundle` |
| `src/sentinel/data/migrations/alembic/versions/004_pipeline_run_snapshot.py` | Additive migration |
| `tests/unit/plugins/prompts/test_prompt_handle.py` | Hash stability, git-SHA fallback, lru_cache clearing |
| `tests/unit/plugins/prompts/test_git_sha_extraction.py` | Env > subprocess > unknown precedence |
| `tests/unit/domain/pipeline/test_replay_bundle.py` | `fetch_replay_bundle` round-trip |
| `tests/unit/domain/pipeline/test_canonical_input_hash.py` | Timestamp exclusion, stable across dict ordering |
| `tests/unit/test_replay_cli.py` | CLI smoke via `capsys` |
| `tests/integration/pipeline/test_investigate_alert_replay_roundtrip.py` | **Golden round-trip — the acceptance test** |

### Modified files
| File | Change |
|------|--------|
| `src/sentinel/plugins/prompts/__init__.py` | Add `PromptHandle`, `_git_sha()`, refactor `load_system_prompt()`; `@lru_cache` |
| `src/sentinel/plugins/prompts/_handle.py` | **DELETED** in Step 11 (was slice 3's shim) |
| `src/sentinel/domain/pipeline/types.py` | Add `ReplayBundle` |
| `src/sentinel/data/tracing_models.py` | Add 8 nullable columns |
| `src/sentinel/domain/pipeline/operations.py` | `persist_pipeline_run()` + `complete_pipeline_run()` accept snapshot fields |
| `src/sentinel/domain/pipeline/queries.py` | Add `fetch_replay_bundle()` + `_canonical_input_hash()` |
| `src/sentinel/domain/pipeline/tracer.py` | `start_pipeline()` takes snapshot params; `complete_pipeline()` takes `final_reply` |
| `src/sentinel/domain/audit/operations.py` | `record_audit_entry()` grows `prompt_sha256` + `pipeline_run_id` params |
| `src/sentinel/data/audit_models.py` | Add `prompt_sha256` + `pipeline_run_id` columns (coordinate with slice 4) |
| `src/sentinel/interfaces/graphs/sre_investigation.py` | `Dependencies` gains `run_id`, `prompt_handle` |
| `src/sentinel/interfaces/graphs/support_review.py` | Same for ticket reviewer |
| `src/sentinel/application/supervisor/orchestrator.py` | Audit entries pass new fields |
| `src/sentinel/worker.py` | Snapshot fields populated at job-dispatch |

## Steps

> Ordered to unblock slice 3 ASAP: **Steps 1–3 land `PromptHandle` and can ship as a standalone PR**.

- [x] **Step 1: Failing tests for `PromptHandle` and `_git_sha()`** — env precedence, subprocess fallback, FileNotFoundError fallback, timeout fallback, version format, cache identity, missing-system-block error. Commit: `test: specify PromptHandle + git-sha extraction contract`

- [x] **Step 2: Implement `PromptHandle` and refactor `load_system_prompt`.** Commit: `feat: PromptHandle carries prompt version and sha256 for replay`

- [x] **Step 3: Migrate all `load_system_prompt` call sites to use `.text`.** Every agent file. Commit: `refactor: agents read PromptHandle.text for system prompts`

> **Merge boundary — slice 3 unblocks here.**

- [x] **Step 4: Failing tests for `_canonical_input_hash`.** Commit: `test: specify canonical input-hash for replay`

- [x] **Step 5: Implement `_canonical_input_hash` helper.** Private in `queries.py`, public re-export in `types.py`. Excluded-keys frozenset: `{"timestamp","received_at","now","run_id","trace_id","pipeline_run_id"}`. Commit: `feat: canonical input-hash for replay bundles`

- [x] **Step 6: Alembic migration.** `004_pipeline_run_snapshot.py` with 8 nullable columns on `pipeline_runs`, plus `audit_log.prompt_sha256` + `audit_log.pipeline_run_id` (skip if slice 4 added first — use `IF NOT EXISTS`). Commit: `feat(data): add replay snapshot columns to pipeline_runs`

- [x] **Step 7: `PipelineRunRecord` + `persist_pipeline_run` take snapshot fields.** Accepts `PromptHandle` for compile-time safety; all defaults `None`. Commit: `feat(pipeline): persist replay snapshot fields on pipeline_runs`

- [x] **Step 8: `ExecutionTracer.start_pipeline` surface + graph wiring.** Entrypoint sequence:
  1. Build `PromptHandle = prompts.load_system_prompt("alert_classifier")`
  2. Build `model_ids`, `mcp_endpoints`, `skill_activations` from `Configuration`
  3. Build `input_hash` via `_canonical_input_hash(alert.model_dump())`
  4. `await tracer.start_pipeline(...)` → `run_id = tracer.pipeline_run_id`
  5. Construct `Dependencies(..., run_id=run_id, prompt_handle=handle, ...)`
  6. `await graph.run(...)`
  7. `await tracer.complete_pipeline(final_reply=...)`
  Commit: `feat(pipeline): thread PromptHandle and run_id through graph dependencies`

- [x] **Step 9: Extend `record_audit_entry`** with `prompt_sha256` + `pipeline_run_id`. `AuditLogRecord` gains columns (coordinate with slice 4). Commit: `feat(audit): capture prompt_sha256 and pipeline_run_id on audit rows`

- [x] **Step 10: `ReplayBundle` + `fetch_replay_bundle(run_id)`.** Single SELECT against `pipeline_runs`; JSON columns deserialised. Raises `ReplayBundleNotFoundError` on miss. Commit: `feat(pipeline): fetch_replay_bundle returns reproducibility snapshot`

- [x] **Step 11: Delete slice 3's `_handle.py` shim.** Re-point imports. Verify `import-linter` green. Commit: `refactor: remove PromptHandle shim superseded by plugins.prompts`

- [x] **Step 12: `src/sentinel/replay.py` CLI scaffold.** `argparse` with positional `run_id`, opens read-only DB, `fetch_replay_bundle`, `json.dumps(attrs.asdict(bundle), indent=2, default=str)`. Non-zero exit on `ReplayBundleNotFoundError`. Header comment flags "**Scaffold only — re-execution is slice 6.**" Commit: `feat: scaffold python -m sentinel.replay CLI`

- [x] **Step 13: CLI smoke test** — `capsys` assertions for happy path + not-found exit code. Commit: `test: replay CLI smoke test`

- [x] **Step 14: Integration golden round-trip test.** 8 functional tests verifying full snapshot round-trip through tracer, persist, and ReplayBundle reconstruction. Commit: `test: golden round-trip for replay bundle snapshot wiring`

- [x] **Step 15: Docs sweep.** PRD §4 + §6 boxes ticked; INDEX.md updated. Commit: `docs: mark prompt versioning steps 1-13 complete`

- [x] **Step 16: Full gate** — 695 tests passing, ruff clean, mypy clean, 3/3 import-linter contracts.

- [x] **Step 17 (bonus): Multi-agent prompt capture.** Migration 005 adds `agent_prompts_json` column. Worker now captures ALL agent prompts per pipeline (not just lead). ReplayBundle gains `agent_prompts` field.

- [x] **Step 18 (bonus): Replay re-execution CLI.** `--replay` and `--diff` flags on `python -m sentinel.replay`. Re-executes pipeline from stored snapshot. Unified diff with exit code 3 on drift.

## Test Plan

### Unit
- `test_git_sha_extraction.py` — 4 cases
- `test_prompt_handle.py` — structure, hash stability, version format, lru_cache identity, missing-block error
- `test_canonical_input_hash.py` — key ordering, timestamp exclusion, datetime formatting, hash shape
- `test_replay_bundle.py` — `fetch_replay_bundle` against seeded row; `ReplayBundleNotFoundError` on miss
- `test_replay_cli.py` — happy + not-found via `capsys`

### Integration (golden)
- `test_investigate_alert_replay_roundtrip.py` — **the acceptance test**. If this fails, replay is broken.

## Acceptance Criteria Mapping

| PRD clause | How ticked |
|------------|------------|
| §4 "Per-pipeline-run snapshot persisted to `pipeline_runs`" | Steps 6–8 + integration test |
| §6 "`prompt_version` + `prompt_sha256` on every `audit_log` row" | Step 9 + integration test |
| §6 "skill files content-hashed; skill activations recorded per run" | Step 8 writes `skill_activations_json` (consumes slice 1) |
| §6 "`replay_pipeline_run(run_id)` helper" | Step 10 (`fetch_replay_bundle`) + Step 12 (CLI); re-execution = future work |

## Risks / Open Questions

1. **Historical git SHA no longer exists.** If squash-merged out of history, `git show` fails. **Mitigation:** we store `prompt_text` on `pipeline_runs` so replay uses literal stored text; `prompt_version` is advisory.
2. **Prompt template changes across replay — replay with stored text or re-render from git?** **Decision: stored text.** Re-rendering from git is fragile (extends/context vars/env config). Byte-exact stored text is the only reliable source. Cost: ~2–5 KB/run.
3. **Storage cost of `prompt_text`.** 10k runs/day × 5 KB = 50 MB/day = ~18 GB/year. Acceptable. Deduplication via `prompt_snapshots(sha256, text)` table is follow-up at 100k runs/day.
4. **`input_hash` exclusion list drift.** New pipeline adding `dispatched_at` breaks replays silently. **Mitigation:** single frozenset; CI test fails if a payload's timestamp field isn't excluded.
5. **`git rev-parse HEAD` subprocess at import time.** `lru_cache(maxsize=1)` + 2s timeout + fallback. Unit tests monkeypatch.
6. **`SENTINEL_GIT_SHA` not set in Docker image.** Would get `"unknown:alert_classifier"`. **Mitigation:** Dockerfile build step must inject `ENV SENTINEL_GIT_SHA=${GIT_SHA}`. Document in `docs/architecture.md`.
7. **Slice 4 (OTel) adding `audit_log.prompt_sha256` first.** Step 6 migration uses `IF NOT EXISTS`; Step 9 is idempotent on ORM side.
8. **`Dependencies` dataclasses growing.** Adding `run_id` + `prompt_handle` pushes to ~15 fields. Both default `None` so no test breaks. Group-object refactor is follow-up.
9. **Concurrent pipeline runs and `lru_cache`.** `load_system_prompt` is process-local; if prompts edited in place during a long-running process, cache hides the change. **Acceptable** — desired property for deterministic replay. Document in CLI header.

## Changes
| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-12 | Steps 1-13 implemented; adapted PromptHandle→PromptTemplate | Existing class extended instead of new class |
| 2026-04-12 | Steps 14-18: golden test, multi-agent capture, replay re-execution | Extended beyond original 16-step plan |

## Outcome

### What was delivered
- `PromptTemplate` extended with git-SHA version (`{sha[:12]}:{template_name}`) and `@functools.cache`
- `canonical_input_hash()` for deterministic replay matching with excluded volatile keys
- Alembic migrations 004+005: snapshot columns on `pipeline_runs`, prompt traceability on `audit_log`, multi-agent prompts
- Full pipeline wiring: `ExecutionTracer` → `persist_pipeline_run` → DB with all snapshot fields
- `ReplayBundle` frozen attrs class + `fetch_replay_bundle()` query
- Multi-agent prompt capture: `agent_prompts_json` stores ALL agent prompts per pipeline
- `record_audit_entry()` gains `prompt_sha256` and `pipeline_run_id`
- `python -m sentinel.replay <run_id>` with `--replay` and `--diff` flags for re-execution
- 695 tests (unit + functional), ruff clean, mypy clean, 3/3 import-linter contracts

### Follow-up / tech debt
- Deduplicate `prompt_text` storage via `prompt_snapshots` table once run volume justifies it
- Refactor `Dependencies` dataclasses into an attrs-frozen group once field count exceeds 20
- Add authentication/authorization to replay CLI for production use
- Store prompt texts per-agent in `agent_prompts_json` (currently only version + SHA-256)
