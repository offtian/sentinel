# Plan: Sentinel Foundations — Phase F1 Config Layering Refactor

**Status:** in-progress
**Created:** 2026-04-25
**Last updated:** 2026-04-25

## Goal

Land the F1 layered configuration substrate the rest of the foundations
phases lean on (`team_id` discriminator, runbook paths, redaction /
approval policies, LiteLLM proxy + Langfuse env vars). The refactor
keeps `BaseConfiguration` (Pydantic `BaseModel`) as the single config
contract — every existing call to `get_config()` keeps returning a
working object. F2/F3/F4/F5/F6 plug into this substrate.

Parent plan:
[`sentinel-hedgefund-foundations.md`](sentinel-hedgefund-foundations.md)
(specifically F1).

## Scope

### In scope

- New env-var fields on `Settings`: `team_profile`, `litellm_base_url`,
  `litellm_virtual_key`, `langfuse_host`, `langfuse_public_key`,
  `langfuse_secret_key`, `otel_collector_endpoint`, `runbooks_root`.
- Layered fields on the existing `BaseConfiguration` carrying firm-wide
  defaults (loop caps, timeouts, confidence thresholds, redaction +
  approval policies, runbook / skills / tool paths, model IDs).
- `team_id` property reading from `settings.team_profile`.
- Frozen policy primitives (`ApprovalPolicy`, `OutputChannel`,
  `RedactionPolicy`) under `data/policies.py`.
- `get_config()` dispatch via a `TEAM_CONFIG_REFS` mapping keyed by
  team profile.
- Move `plugins/config.py` → `plugins/common/config.py`. Update every
  caller import.
- Unit tests covering the new fields, primitives, and dispatch.
- `.env.default` and `docs/architecture.md` updates.

### Out of scope

- Separate `BaseConfig` and `CommonConfig` classes — the consolidation
  pivot put the layered fields on `BaseConfiguration` directly. Adding
  more inheritance layers earns its keep when teams actually diverge.
- `SRETeamConfig` (and sibling team configs) — `team_id` reads from
  settings; subclasses arrive when DevOps / ACE land.
- DevOps and ACE team profiles — `get_config()` raises
  `NotImplementedError` if `team_profile != "sre"`.
- Wiring the new env vars into runtime behaviour (Langfuse exporter,
  LiteLLM proxy transport, runbook loader) — F4 / F5 / F6.
- F0 stakeholder ADR closure — proceeding under RFC v0.4 defaults.

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Type system | Stay on Pydantic `BaseModel` for the config contract. | Existing project convention; simpler than mixing attrs and Pydantic across the config layer. Pydantic also gives validation and FastAPI ergonomics. |
| Layered fields | Live directly on `BaseConfiguration` with firm-wide defaults. | Collapses the original 4-layer chain (`Settings → BaseConfig → CommonConfig → *TeamConfig`) to two practical layers: `BaseConfiguration` (shape + shared defaults) → `CommonConfiguration` (vendor wiring). Teams differ via settings until divergent behaviour earns the subclass. |
| `team_id` | Property reading `settings.team_profile`. | One source of truth for the discriminator. Subclasses can override with a hardcoded literal once profiles diverge. |
| Policy primitives | `attrs.frozen` dataclasses in `src/sentinel/data/policies.py`. | `data/` sits below `config` in the import-linter layer order, so `config.py` can import them with no layer reshuffle. The primitives are inert placeholders today (no consumer reads them yet), so a richer domain home isn't earned. |
| `CommonConfiguration` location | Move to `src/sentinel/plugins/common/config.py`. | Aligns with the future shared-substrate layout (`plugins/common/runbooks/`, `plugins/common/skills/`). Six test imports updated to match. |
| `get_config()` dispatch | `TEAM_CONFIG_REFS: dict[TeamId, str]` mapping team profile to `"module:Class"` strings, resolved via importlib. | Cleaner than the previous `_CONFIG_CLASS_PATH` + `_CONFIG_CLASS_NAME` pair; future team profiles add an entry to the dict. |
| Test pattern | Construct `BaseConfiguration(settings=Settings())` directly; no `os.environ` mutation. | Matches existing test fixtures; deterministic. |

## Steps

- [x] **Step 1 — Policy primitives (was F1.4).** Add
  `src/sentinel/data/policies.py` with frozen `ApprovalPolicy`,
  `OutputChannel`, `RedactionPolicy`. RED-first unit tests.
- [x] **Step 2 — Layered fields on `BaseConfiguration` (was F1.1 + F1.3).**
  Add the layered fields with firm-wide defaults. Add a `team_id`
  property reading `settings.team_profile`.
- [x] **Step 3 — Settings additions (was F1.2).** New env-var fields on
  `Settings` (`team_profile`, `litellm_*`, `langfuse_*`,
  `otel_collector_endpoint`, `runbooks_root`).
- [x] **Step 4 — `get_config()` dispatch (was F1.7).** Replace the
  `_CONFIG_CLASS_PATH` / `_CONFIG_CLASS_NAME` pair with a
  `TEAM_CONFIG_REFS` mapping. Unknown profiles raise
  `NotImplementedError`.
- [x] **Step 5 — Move `CommonConfiguration` (was F1.9).** `git mv
  plugins/config.py plugins/common/config.py`. Update six test
  imports.
- [x] **Step 6 — Unit tests (was F1.10).** 17 tests in
  `tests/unit/test_config_layering.py` cover primitives, Settings
  additions, and the new fields on `BaseConfiguration`.
- [ ] **Step 7 — Documentation (was F1.11).** Update `.env.default`
  with the new env-vars. Update `docs/architecture.md` Configuration
  section to reflect the consolidated chain.
- [ ] **Step 8 — Final verification (was F1.12).** `just lint && just
  test` green; smoke `just run-api` to confirm existing pipelines
  still run.

### Dropped from F1 scope (the consolidation pivot)

- Separate `BaseConfig` (attrs.frozen) class — the layered fields
  landed on the existing `BaseConfiguration` Pydantic class.
- Separate `CommonConfig` class in `plugins/common/common.py` — shared
  defaults live on `BaseConfiguration` itself.
- `SRETeamConfig` and the `plugins/teams/sre/` tree — not needed
  while `team_id` derives from settings. Re-introduce when team
  profiles need divergent behaviour.
- Import-linter contracts for `plugins/teams/*` — no `plugins/teams/`
  tree exists.
- Deprecated `CommonConfiguration` alias — the rename happened
  in-place; existing imports updated rather than aliased.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-25 | Initial draft (4-layer chain `Settings → BaseConfig → CommonConfig → SRETeamConfig`). | Original RFC v0.4 §15 layout. |
| 2026-04-25 | Step ordering re-arranged (F1.4 before F1.1). | `BaseConfig` field types referenced primitives. |
| 2026-04-25 | Primitives moved from `plugins/common/` to `domain/` per project layering. | "Plugins are for vendor-specific stuff" guidance. |
| 2026-04-25 | Primitives moved again from `domain/` to `data/policies.py`. | `data/` already sits below `config` in the layer order; avoids a layer reshuffle. |
| 2026-04-25 | Pivoted away from new `BaseConfig` (attrs.frozen). Layered fields landed directly on the existing Pydantic `BaseConfiguration`. | "Pydantic basemodel is so much better" — collapses parallel types into one. |
| 2026-04-25 | Dropped `CommonConfig` and `SRETeamConfig`. | Without divergent team behaviour, the extra inheritance layers are unearned. |
| 2026-04-25 | Moved `plugins/config.py` → `plugins/common/config.py`. Replaced `_CONFIG_CLASS_PATH` / `_CONFIG_CLASS_NAME` with `TEAM_CONFIG_REFS` registry. | Cleaner dispatch; aligns with the future shared-substrate layout. |

## Outcome

_Fill in after completion._

### What was delivered

- ...

### Follow-up / tech debt

- Re-introduce `SRETeamConfig` (and DevOps / ACE siblings) when team
  profiles need divergent behaviour — runbook paths, allowed tools,
  output channels, etc. The `BaseConfiguration` layered fields already
  carry the right shape; the subclass just overrides the values.
- Tighten F0 ADRs (`0001`–`0006`) once stakeholder conversations
  conclude. F1 assumes RFC v0.4 default positions hold; flips would
  require revisiting (most plausibly O-10 PydanticAI ↔ alternative
  framework which would discard F1 entirely).
- Wire infra-client cached properties (LiteLLM proxy, Langfuse client,
  DB session factory) onto `BaseConfiguration` once F4 / F5 land their
  consumers. The fields and env-vars exist; only the lazy clients are
  pending.
