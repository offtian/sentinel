---
paths:
  - "src/**/*.py"
---

# Application & Architecture Conventions

## Layered Architecture
- `interfaces/` — FastAPI routes, Pydantic Graph pipelines, PydanticAI agents, webhooks. Translate interface events into domain calls. NO business logic here.
- `application/` — orchestrate use-case journeys. Single entry point per use-case.
- `domain/` — reusable business logic, agnostic of which use-case calls it.
- `data/` — SQLModel tables plus frozen primitives shared between `config` and `domain` (policy types, identity envelopes, capability tokens, discriminator enums, anything that both layers must agree on without one importing the other). Models stay thin (no business logic); primitives are pure data shapes (no vendor binding, no I/O).
- `config/` (`src/sentinel/config.py`) sits above `data/` and below `domain/`/`application/`. Composes `data/` primitives onto `BaseConfiguration` for every layer above to consume.
- Layer boundaries enforced by import-linter contracts in `pyproject.toml` — lower layers cannot import higher layers.

## Domain Layer Structure
- Package by domain category: `domain/$category/`
- Read-only: `queries.py`, Write: `operations.py`
- For queries: categorise by the type of object returned
- For operations: categorise by the primary object being operated on

## Application Layer Structure
- Public functions MUST use keyword-only args: `def do_thing(*, foo, bar):`
- Public functions MUST have docstrings with params, return types, and raisable exceptions
- Prefix private modules with `_` (e.g. `_trigger.py`)

## Exception Handling
- Distinguish anticipated vs unanticipated exceptions
- Anticipated: handle gracefully, don't log to Sentry
- Unanticipated: log to Sentry with `logger.exception("descriptive message")` — Sentry picks up the exception automatically
- NEVER format exception message into the log message — pass data as logger args: `logger.exception("Failed for %s", x)`
- Exception classes must be importable from the same module as the function that raises them
- Prefer defining exceptions in the module where they're raised, avoid shared `exceptions.py`

## System Clock
- Minimise `now()`/`today()` calls in domain layer
- Compute dates at interface layer, pass them in as explicit parameters
- Never use `def fn(*, base_date=None):` defaulting to today — require the caller to pass it

## Keyword Arguments
- Always use kwargs when calling functions where positional args aren't obvious
- Always use keyword-only args (`*`) for public domain/application functions

## Configuration Architecture

### Two-layer config chain (RFC §15.4)

Three modules, in dependency order:

- `settings.py` — env-var ingestion only via `pydantic-settings.BaseSettings`. Bootstrap layer; no business logic, no derived values, no vendor wiring. Singleton via `get_settings()`.
- `config.py` — `BaseConfiguration` (Pydantic `BaseModel`) declares the layered shape every team config carries: loop caps, timeouts, confidence thresholds, runbook paths, model IDs, plus composed policy primitives (`ApprovalPolicy`, `RedactionPolicy`, `OutputChannel` tuple). Carries firm-wide defaults so an unconfigured profile is still safe-by-default.
- `plugins/common/config.py` — `CommonConfiguration(BaseConfiguration)` is the concrete subclass that wires vendor adapters, searchers, toolsets, and agents (`load_vendors()`, `load_agents()`, `build_*()` methods). Returned by `get_config()` for every team profile that doesn't yet need a custom subclass.

Subclasses (`SRETeamConfig`, `DevOpsTeamConfig`, `ACETeamConfig` in `plugins/teams/<name>/config.py`) only earn their keep when team behaviour actually diverges — overriding `team_id`, allowed tools, output channels, runbook paths, or `build_*()` methods. Until then, leave the team on `CommonConfiguration` and let `team_id` derive from `settings.team_profile`.

### Multi-tenant dispatch

- `Settings.team_profile: Literal["sre", "devops", "ace"]` is the discriminator. One source of truth for which team profile is active.
- `TEAM_CONFIG_REFS: dict[TeamId, "module:Class"]` registers the concrete config per profile. `_build_default_config()` resolves the entry via `importlib.import_module` so the `config` layer never imports the `plugins` layer statically.
- Unknown team profiles raise `NotImplementedError` with a pointer to the registry — explicit unimplemented surface, not a silent default.
- `BaseConfiguration.team_id` is a `@property` reading `settings.team_profile`. Subclasses override with a hardcoded literal once the profile diverges.

### Domain ↔ config integration

- Domain code reads policies, thresholds, and runbook paths off `get_config()` (a `BaseConfiguration`), **never directly off `Settings`**. `BaseConfiguration` is the runtime contract every layer above `data/` depends on; `Settings` is implementation detail of how those values were sourced.
- New env-var-driven knobs land on `Settings`, then surface on `BaseConfiguration` via a `@property` (mirrors the existing `classifier_model`, `analyser_model` pattern). **No knob exists as a writable field on both** — that invites silent drift.
- Policy primitives compose onto `BaseConfiguration` as Pydantic fields with `Field(default_factory=Primitive.default)` (firm-wide safe values) or `Field(default_factory=Primitive.empty)` (fail-closed placeholder).
- Vendor adapters, agents, and infra clients (LiteLLM proxy, Langfuse) live on `CommonConfiguration` (or team subclasses), not `BaseConfiguration` — `BaseConfiguration` stays free of vendor binding so test fixtures can construct it without a vendor environment.
