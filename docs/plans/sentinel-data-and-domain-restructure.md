# Plan: Data Layer Reorganisation + Domain Alerts/Investigations Split

**Status:** draft
**Created:** 2026-04-25
**Last updated:** 2026-04-25
**Branch:** `refactor/sentinel-domain-alerts-investigations`
**Worktree:** `.worktrees/domain-rename`
**Base:** `main` @ `7426f91` (post F1 + F2 merges)

## Goal

Two coupled refactors that align the codebase with RFC-001's vocabulary and the layer rules already documented in `.claude/rules/python.md` + `application.md`:

1. **`data/` reorganisation** — split the flat `data/` folder into `data/sql/` (SQLModel tables) and `data/primitives/` (frozen `attrs` shapes), keeping DB infra at the top level. Today both kinds of file live as siblings; the distinction (rule-load-bearing) is invisible.
2. **`domain/sre/` split** — replace the misnamed `domain/sre/` (which holds team-agnostic alert + investigation logic, not SRE-team code) with `domain/alerts/` (Alert payload + severity) and `domain/investigations/` (Investigation, Finding, status, adapters, ops, queries). Per RFC §15.1, "sre" is one of three team profiles (`sre`, `devops`, `ace`) — team-specific code lives under `plugins/teams/<profile>/`, **not** under `domain/`.

Why now: F2 (envelope) is merged so the `Envelope` primitive is settled. F3 (DB schema) is next on the foundations plan and will add tables — better to land it on a clean `data/` layout than to refactor afterwards. The domain split removes a vocabulary footgun before more pipelines and team profiles arrive.

## Scope

### In scope

- Split `src/sentinel/data/` into:
  - `data/sql/` — SQLModel tables (`models.py`, `audit_models.py`, `evaluation_models.py`, `job_models.py`, `tracing_models.py`)
  - `data/primitives/` — frozen attrs shapes (`envelope.py`, `policies.py`)
  - `data/` (top level) — DB infrastructure (`database.py`, `db.py`, `__init__.py`, `alembic.ini`, `migrations/`)
- Split `src/sentinel/domain/sre/` into:
  - `domain/alerts/entities.py` — `Alert`, `AlertSeverity`
  - `domain/investigations/entities.py` — `Finding`, `InvestigationStatus`, `Investigation`
  - `domain/investigations/adapters.py` — `BaseInvestigationAdapter`, `K8sInvestigationAdapter`, `AuditEntry`, `InvestigationContext`, `InvestigationResult` (currently `domain/sre/investigation.py`)
  - `domain/investigations/holmes_adapter.py`, `kagent_adapter.py`, `k8s_native_agent.py`
  - `domain/investigations/operations.py`, `queries.py`
- Rename pipeline labels and the graph file:
  - `interfaces/graphs/sre_investigation.py` → `interfaces/graphs/investigation.py`
  - `JobType.SRE_INVESTIGATION` → `JobType.INVESTIGATION`
  - `pipeline_type="sre_investigation"` → `pipeline_type="investigation"` (replay bundle key)
  - `pipeline="sre"` (graph node telemetry) → `pipeline="investigation"`
- Update **all** imports across `src/` and `tests/` (~35 files in `src/`, ~17 files in `tests/`).
- Mirror the test directory layout: `tests/unit/domain/sre/*` → `tests/unit/domain/alerts/*` + `tests/unit/domain/investigations/*`.
- Update import-linter contracts in `pyproject.toml` if needed (none currently mention `sre` — verify post-rename).
- Update docs (`README.md`, `docs/architecture.md`, plan files referencing `sre/`, AGENTS.md import example).

### Out of scope

- **API route prefix `/api/sre/*`** — kept as-is. These are the SRE team's intake URLs (PagerDuty/Datadog post here, the SRE team owns the channel). Routes are TEAM-scoped surface, not DOMAIN-misnamed. Future `/api/devops/*` and `/api/ace/*` will sit alongside.
- **Settings `SRE_AUTO_INVESTIGATE`, `SRE_SLACK_CHANNEL`** — kept as-is for the same reason. Once F3+ adds `DEVOPS_*` / `ACE_*` parallels these become a recognisable team-namespaced family.
- **`TeamId = Literal["sre","devops","ace"]`** — keep "sre" literal. This is genuine team identity.
- **DB schema changes** — none. The `InvestigationRecord` SQLModel keeps its column names; only its file location moves.
- **Migrations** — none. No new tables, no column renames; the F3 schema work is a separate plan.
- **`plugins/teams/<sre|devops|ace>/` skeletons** — RFC §15.1 prescribes these, but they're a separate plan (`sentinel-team-plugins-skeleton`).

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Domain folder names | `domain/alerts/` + `domain/investigations/` | RFC §3.5 names `Investigation` as the central aggregate. RFC §3.2 names the entry-point shape `IngestedAlert`. Splitting cleanly maps onto stage 1 vs stage 4 of the pipeline. User confirmed this split. |
| Reject `domain/incidents/` | Excluded | RFC never uses "incident" for the internal artifact (PagerDuty incidents are external, normalised into `IngestedAlert`). |
| Pipeline label name | `pipeline="investigation"` (singular) | Matches the graph file (`investigation.py`) and the aggregate (`Investigation`). Avoids plural/folder mismatch. |
| `data/` subfolder names | `sql/` + `primitives/` | User suggested `sql/` over `tables/` — clearer to readers. `primitives/` matches the term used in `python.md` rules (over `dataclass/`, since these are `attrs.frozen` not `@dataclass`). |
| DB infrastructure location | Stays at top of `data/` | `database.py`, `db.py`, `migrations/`, `alembic.ini` are infrastructure, not data shapes. Demoting them into a subfolder buys nothing and breaks alembic's cwd assumptions. |
| Adapter file name | `adapters.py` (was `investigation.py`) | Today `domain/sre/investigation.py` defines the ABC + result types — the filename collides with the folder concept. `adapters.py` is what it actually contains. |
| Test layout | Mirror `src/` exactly | `tests/unit/domain/alerts/test_entities.py`, `tests/unit/domain/investigations/test_*.py`. `tests/unit/data/sql/`, `tests/unit/data/primitives/`. Keeps mental model 1:1. |
| Keep `SRE_*` env vars | Yes | They configure SRE-team behaviour. Future `DEVOPS_AUTO_INVESTIGATE` will sit beside them. |
| Keep `/api/sre/*` routes | Yes | Public webhook URLs; renaming breaks PagerDuty/Datadog integrations. SRE-team intake is a defensible team surface. |
| Module-import convention | `from sentinel.domain.investigations import entities as investigation_entities` | Per `python.md` — import modules, not objects. Same for `alert_entities`, `investigation_adapters`. |
| Import order — does `domain/investigations/` import `domain/alerts/`? | Yes | `Investigation` has `alert: Alert` field; sibling import within the same layer is fine (no layer rule violated). |
| Atomic vs phased commits | One commit per phase below | Each phase keeps the codebase green. PR is one branch, multiple atomic commits per `git-workflow.md`. |
| Graph rebuild after rename | Yes | Run `_rebuild_code` per CLAUDE.md graphify rule once rename is complete. |

## Steps

Each phase ends green (`just lint && just test`). Sequence is chosen so each phase is independently revertable.

### Phase A — `data/` reorganisation (foundation)

- [ ] A.1 Create `src/sentinel/data/sql/__init__.py` and `src/sentinel/data/primitives/__init__.py`.
- [ ] A.2 Move SQLModel files: `data/models.py` → `data/sql/records.py`; `audit_models.py` → `data/sql/audit.py`; `evaluation_models.py` → `data/sql/evaluation.py`; `job_models.py` → `data/sql/jobs.py`; `tracing_models.py` → `data/sql/tracing.py`. (Filename suffix `_models` becomes redundant under `sql/`.)
- [ ] A.3 Move primitives: `data/envelope.py` → `data/primitives/envelope.py`; `data/policies.py` → `data/primitives/policies.py`.
- [ ] A.4 Update every import in `src/` and `tests/`. Examples:
  - `from sentinel.data import models` → `from sentinel.data.sql import records as records_models` (or just `records`)
  - `from sentinel.data import envelope` → `from sentinel.data.primitives import envelope`
  - `from sentinel.data import policies` → `from sentinel.data.primitives import policies`
- [ ] A.5 Update `data/migrations/alembic.ini` and `data/migrations/env.py` if they reference moved table modules by string path.
- [ ] A.6 Mirror tests: `tests/unit/data/test_db.py` stays; create `tests/unit/data/sql/`, `tests/unit/data/primitives/` and relocate `test_evaluation_models.py` and any envelope/policies tests under the relevant folder.
- [ ] A.7 Run `just lint && just test` — must be green.
- [ ] A.8 Commit: `refactor(data): split data/ into sql/ + primitives/ subfolders`.

### Phase B — `domain/alerts/` + `domain/investigations/` split (RED → GREEN)

TDD discipline per `principles.md`: Phase B uses RED → GREEN per move because the rename touches structural code and we want a failing test pinning each new module before content lands.

- [ ] B.1 (RED) Add `tests/unit/domain/alerts/test_entities.py` importing from `sentinel.domain.alerts import entities` and asserting Alert/AlertSeverity exist with the expected fields. Confirm import error.
- [ ] B.2 (GREEN) Create `src/sentinel/domain/alerts/__init__.py` and `entities.py` with `Alert`, `AlertSeverity` moved from `domain/sre/entities.py`. Test passes.
- [ ] B.3 (RED) Add `tests/unit/domain/investigations/test_entities.py` importing `Finding`, `Investigation`, `InvestigationStatus`. Confirm import error.
- [ ] B.4 (GREEN) Create `src/sentinel/domain/investigations/__init__.py` and `entities.py` with the three classes moved from `domain/sre/entities.py`. The `Investigation.alert: Alert` field imports from `sentinel.domain.alerts import entities as alert_entities`. Test passes.
- [ ] B.5 Move adapters: `domain/sre/investigation.py` → `domain/investigations/adapters.py` (rename file too — its content is the adapter ABC + result types, not "investigation"). Update internal `from sentinel.domain.sre import entities` to `from sentinel.domain.investigations import entities` and `from sentinel.domain.alerts import entities as alert_entities`.
- [ ] B.6 Move `holmes_adapter.py`, `kagent_adapter.py`, `k8s_native_agent.py` to `domain/investigations/`. Update internal imports.
- [ ] B.7 Move `operations.py`, `queries.py` to `domain/investigations/`. Update internal imports.
- [ ] B.8 Update **every** import outside the moved files (≈35 files in `src/`, ≈17 in `tests/`). Search-and-rewrite map:
  - `from sentinel.domain.sre import entities` → split into `from sentinel.domain.alerts import entities as alert_entities` and/or `from sentinel.domain.investigations import entities as investigation_entities` (depending on which classes the file uses)
  - `from sentinel.domain.sre import investigation` → `from sentinel.domain.investigations import adapters`
  - `from sentinel.domain.sre import holmes_adapter` → `from sentinel.domain.investigations import holmes_adapter`
  - `from sentinel.domain.sre import kagent_adapter` → `from sentinel.domain.investigations import kagent_adapter`
  - `from sentinel.domain.sre import k8s_native_agent` → `from sentinel.domain.investigations import k8s_native_agent`
  - `from sentinel.domain.sre import operations` → `from sentinel.domain.investigations import operations`
  - `from sentinel.domain.sre import queries` → `from sentinel.domain.investigations import queries`
  - Local aliases like `as sre_entities`, `as sre_ops`, `as sre_queries` get renamed to their new aliases.
- [ ] B.9 Mirror tests: rename `tests/unit/domain/sre/` to two folders (`tests/unit/domain/alerts/` containing `test_entities.py` for Alert tests; `tests/unit/domain/investigations/` containing the rest). Update every test's imports.
- [ ] B.10 `tests/factories/__init__.py` — `make_alert()` factory builds `alert_entities.Alert`; `make_investigation()`/`make_finding()` build from `investigation_entities`. Imports updated.
- [ ] B.11 Delete the now-empty `src/sentinel/domain/sre/` directory and `tests/unit/domain/sre/` (verify both are empty first).
- [ ] B.12 Run `just lint && just test` — must be green.
- [ ] B.13 Commit: `refactor(domain): split domain/sre/ into domain/alerts/ + domain/investigations/`.

### Phase C — Pipeline label + graph file rename

- [ ] C.1 Rename `src/sentinel/interfaces/graphs/sre_investigation.py` → `src/sentinel/interfaces/graphs/investigation.py` (`git mv`).
- [ ] C.2 Update internal `pipeline="sre"` constants in graph nodes → `pipeline="investigation"`.
- [ ] C.3 Find and update the `JobType` enum: `SRE_INVESTIGATION = "sre_investigation"` → `INVESTIGATION = "investigation"` (note: this also changes the *value*, which is the replay-bundle `pipeline_type` key, so update worker dispatch + replay restoration in the same commit).
- [ ] C.4 Update `worker.py` dispatch: `if job_type == entities.JobType.SRE_INVESTIGATION.value` → `... .INVESTIGATION.value`. Also `pipeline_type="sre_investigation"` → `pipeline_type="investigation"`.
- [ ] C.5 Update `replay.py`: `if bundle.pipeline_type == "sre_investigation"` → `== "investigation"`.
- [ ] C.6 Update the FastAPI route handler that triggers the pipeline (the `/api/sre/investigate` endpoint kicks `JobType.INVESTIGATION` — only the enum name changes, not the route path).
- [ ] C.7 Search-and-update tests for `pipeline_type="sre_investigation"`, `JobType.SRE_INVESTIGATION`, and graph file imports.
- [ ] C.8 Mirror test rename: `tests/unit/interfaces/graphs/test_sre_*.py` → `test_investigation_*.py`. Internal imports updated.
- [ ] C.9 Backwards-compat for in-flight replay bundles? **No** — RFC says replay is an internal debugging tool, no production users. Stale bundles will fail with a clear `KeyError` rather than silently doing the wrong thing. Document in plan changelog.
- [ ] C.10 Run `just lint && just test` — must be green.
- [ ] C.11 Commit: `refactor(pipeline): rename sre_investigation → investigation across graph, JobType, and replay`.

### Phase D — Docs sweep

- [ ] D.1 Update `README.md`:
  - Project Structure block: `domain/` description.
  - "SRE Investigation Pipeline" diagram → "Alert Investigation Pipeline" (or "Investigation Pipeline").
  - API table can stay (routes unchanged).
- [ ] D.2 Update `docs/architecture.md` — layer diagram, pipeline section, any references to `domain/sre/`.
- [ ] D.3 Update `AGENTS.md` quick-reference example — `from sentinel.domain.sre_entities` → use `from sentinel.domain.alerts` (more representative of new structure).
- [ ] D.4 Update `docs/plans/INDEX.md` to add this plan.
- [ ] D.5 Skim `docs/plans/sentinel-hedgefund-foundations.md` and `Sentinel/RFC-001-sentinel-hedgefund.md` for stale `domain/sre/` references — update or annotate.
- [ ] D.6 Update PRD checkboxes if any acceptance criteria mention `domain/sre/` paths.
- [ ] D.7 Run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to refresh `graphify-out/` per CLAUDE.md.
- [ ] D.8 Commit: `docs: update structure and pipeline references after data/domain restructure`.

### Phase E — Validation + PR

- [ ] E.1 Run full local validation:
  - `just lint` (ruff + mypy + import-linter)
  - `just test` (unit)
  - `just test-integration` (DB-backed, optional locally; CI will run)
- [ ] E.2 Diff against `main`: `git diff origin/main...HEAD --stat` — sanity check file count.
- [ ] E.3 Push branch: `git push -u origin refactor/sentinel-domain-alerts-investigations`.
- [ ] E.4 Open PR with title: `refactor: split data/ into sql+primitives, rename domain/sre → alerts+investigations`. Body includes:
  - Why (RFC §15.1 vocabulary, F3 prep)
  - What stays (`/api/sre/*`, `SRE_*` env vars, `TeamId="sre"`)
  - Migration impact: none (no schema, no API changes)
  - Test plan: 791+ tests must pass; verify import-linter contracts unchanged.

## What stays as `sre` (intentional, do not rename)

| Surface | Example | Why |
|---------|---------|-----|
| `TeamId` literal | `Literal["sre","devops","ace"]` | Genuine team identity |
| `Settings.team_profile` | `team_profile: TeamId = "sre"` | Per-tenant discriminator |
| API route prefix | `/api/sre/webhooks/pagerduty` | SRE-team intake; team-scoped surface |
| Env vars | `SRE_AUTO_INVESTIGATE`, `SRE_SLACK_CHANNEL` | Per-team behaviour knobs |
| Intent router enum | `Intent.SRE = "sre"` | Routes chat queries to SRE team logic |
| `TEAM_CONFIG_REFS` keys | `"sre": "sentinel.plugins.common.config:CommonConfiguration"` | Team profile registry |
| Future `plugins/teams/sre/` | (RFC §15.1) | Real team-specific code goes here |
| Runbook folder | Future `plugins/teams/sre/runbooks/` | Per-team runbook catalogue |

## Risks

1. **Migration of import path `data.models` → `data.sql.records`** — alembic env.py historically references `target_metadata = SQLModel.metadata`; if any migration imports the table class by module path (e.g. `from sentinel.data.models import InvestigationRecord`), that line must be updated, otherwise migrations break. Mitigation: audit `data/migrations/` files in step A.5.
2. **JobType enum value change** (`"sre_investigation"` → `"investigation"`) is a stored-string change in any persisted job rows. Mitigation: there are no production deployments (development-stage repo per repo notes); accept the break and document in the commit message. If a deployed instance exists, this becomes a separate migration plan — flag for user before C.3.
3. **Graph file rename + telemetry label change** — span attributes containing `pipeline="sre"` will appear with `pipeline="investigation"` post-deploy; any dashboards filtering on the old value need updating. Mitigation: mention in PR body so the user updates dashboards (no Sentinel dashboards exist yet per RFC tracking).
4. **`tests/factories/__init__.py` is a load-bearing import target** — many tests do `from tests.factories import make_alert`. The factory's *internal* imports change but the public exports do not. Verify by running tests after Phase B.
5. **Worktree env quirk** — psycopg binary needed reinstalling on the worktree venv (arm64 vs x86_64); already fixed. Future worktrees off this branch should be aware.

## Open questions for confirmation

1. **JobType value** — change the enum **value** from `"sre_investigation"` to `"investigation"`? (Risk #2 above.) Or keep the value, just rename the symbol `JobType.SRE_INVESTIGATION → JobType.INVESTIGATION`? Renaming the value is cleaner; keeping it is safer if any persisted rows exist.
2. **Adapter filename** — is `domain/investigations/adapters.py` the right name, or should it stay `investigation.py` (consistent with current naming)? `adapters.py` better describes content (ABC + result types), but creates a slight inconsistency vs. how other domains name their files.
3. **`data/sql/records.py` filename** — currently `data/models.py` holds two records (`InvestigationRecord` + `TicketReviewRecord`). Keep them together as `records.py`, or split into `data/sql/investigations.py` + `data/sql/tickets.py`? Splitting reads better when more record types arrive in F3.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-25 | Plan drafted | Initial draft; awaiting confirmation on open questions |

## Outcome

_Fill in after completion._
