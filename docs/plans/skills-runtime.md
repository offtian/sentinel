# Plan: Skills Runtime

**Status:** in progress (Phases 1–5 complete; Phase 7 config-driven refactor in progress)
**Created:** 2026-04-08
**Last updated:** 2026-04-08

> **For agentic workers:** REQUIRED SUB-SKILL — use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the on-disk Skills catalogue + runtime loader so every Sentinel agent picks up procedural runbook knowledge uniformly, driven by classifier `category` outputs, with deterministic ordering and content hashing for replay/audit slices to consume later.

**Slice owns:** Skills directory layout, loader, system-prompt injection helper, agent wiring (5 agents), seed catalogue, `list_skills` MCP tool, `skill_activated` structlog event, and the unit/integration test suite for the above. Out of scope: prompt caching markers, OTel export, audit-log persistence, `Configuration.build_mcp_toolsets()`, and `PromptHandle` versioning.

**Tech Stack:** Python 3.13, attrs (frozen), PyYAML (frontmatter), hashlib (SHA-256), FastMCP, structlog, Pydantic Graph / PydanticAI agents.

**PRD section tracked:** `docs/prd.md` section 7 — boxes ticked by this slice are the `plugins/skills/<name>/SKILL.md` layout, `load_skills_for(...)`, the prompt-append helper, and `list_skills` MCP tool. Section 1 & 2 boxes about classifier-driven runbook/response selection become unblocked.

---

## Goal

Sentinel agents currently load only a Jinja system prompt. To enable runbook-driven reasoning ("on a `k8s_crashloop` alert, follow these steps"), we need a runtime that loads small Markdown "Skills" from disk, filters them by the classifier `category` already produced upstream, and concatenates them onto every agent's system prompt deterministically. The loader must produce a stable SHA-256 of the activated set so future slices (telemetry, audit, replay) can record and replay exactly which knowledge an agent saw — without changing this slice.

## Scope

### In scope
- `src/sentinel/plugins/skills/` package with `<name>/SKILL.md` files (YAML frontmatter + Markdown body)
- `plugins.skills.load_skills_for(*, category, max)` returning a deterministic `tuple[SkillHandle, ...]`
- `SkillHandle` frozen attrs class: `name`, `version`, `description`, `applies_to`, `body`, `sha256`
- `interfaces/graphs/agents/utils.py::append_skills_to_prompt(*, base_prompt, category, max_skills)` helper
- Wire helper into `alert_classifier`, `root_cause_analyser`, `ticket_reviewer`, `response_drafter`, `chart_generator` agents (NOT `k8s_investigator`)
- Seed catalogue files (filenames only, contents drafted by SRE team):
  - `k8s-crashloop-runbook/SKILL.md`
  - `database-connection-runbook/SKILL.md`
  - `latency-spike-runbook/SKILL.md`
  - `auth-error-response/SKILL.md`
  - `rate-limit-response/SKILL.md`
  - `chart-helm-best-practices/SKILL.md`
- `list_skills` FastMCP tool on `interfaces/mcp/server.py`
- `skill_activated` structlog event emitted on every load (one per skill, with `name`, `version`, `sha256`, `category`)
- Unit tests (loader determinism, frontmatter parsing, SHA-256 stability, glob/exact `applies_to` matching, empty-category fallback, `max` truncation) and an integration test for `list_skills`

### Out of scope (other slices own these)
- Prompt caching markers (slice 3)
- OTel exporter / audit log persistence of skill hashes (slices 4 & 5)
- `Configuration.build_mcp_toolsets()` (slice 2)
- `PromptHandle` versioning of Jinja templates (slice 5)
- `k8s_investigator` wiring (already done as the reference implementation)

## Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Skill on-disk format | `<name>/SKILL.md` with YAML frontmatter (`---` fenced) + Markdown body | Mirrors Anthropic "Claude Skills" convention so engineers can copy public runbooks; one directory per skill leaves room for future asset files without breaking the schema |
| Frontmatter parser | `yaml.safe_load` (PyYAML already in deps for Helm) | No new dep, defends against arbitrary tag execution |
| `applies_to` semantics | `list[str]` of fnmatch globs (e.g. `["k8s_*", "kubernetes_crashloop"]`); empty list means "wildcard, applies to all categories" | Matches free-form snake_case category strings; globs cover families without enumerating every subtype; explicit empty list ⇒ universal so an `org-style-guide` skill can apply everywhere |
| Category matching | Case-insensitive `fnmatch.fnmatchcase(category.lower(), pattern.lower())` over the union of patterns | Cheap, stdlib, deterministic, no regex-injection surprises |
| `SkillHandle` shape | `attrs.frozen` with `name: str`, `version: str`, `description: str`, `applies_to: tuple[str, ...]`, `body: str`, `sha256: str` | Immutable per AGENT.md; `tuple` over `list` per conventions |
| Hashing strategy | `hashlib.sha256(raw_file_bytes).hexdigest()` over the **raw file bytes** including frontmatter | Single canonical bytes-in → hash-out pipeline; future slices can persist and recompute without ambiguity |
| Deterministic sort key | `(skill.name,)` ascending — name is unique because it's the directory name | Stable across OS file iteration order; alphabetical is the simplest contract for replay |
| `max` truncation order | Sort first, then truncate to `[:max]` | Replay slice can re-derive the exact set from `(category, max)` alone |
| Empty-category fallback | If no skill matches, return `()` and emit a single `skills_no_match` log event — never raise | Agents must keep working even when the catalogue is empty; tests assert this contract |
| Loader cache | Module-level `functools.lru_cache(maxsize=1)` over `_load_all_skills()`; `load_skills_for` filters in memory | Skills are static at process-start; disk I/O happens once per worker. Tests use `cache_clear()` in fixtures |
| Where the helper lives | `interfaces/graphs/agents/utils.py` | Symmetric with the existing prompt utility module; keeps agent files thin |
| How the helper is called | Static `SYSTEM_PROMPT = ...` for fixed-category agents; `@agent.system_prompt` async injection for dynamic-category agents | PydanticAI supports both; per-call `lru_cache` hit is O(1) |
| Seed content | Placeholder bodies only (`# {{name}}\nTODO: SRE team to fill`) — real prose in a separate PR | Keeps loader/integration tests deterministic |
| Telemetry event | `logs.log_event("skill_activated", params={"skill_name", "version", "sha256", "category"})` | Audit-log slice tails structlog and persists; this slice only emits |
| `list_skills` MCP tool | Returns `[{name, version, description, applies_to}]` (no body) sorted by name | Mirrors `search_documentation` shape; keeps bodies off the wire |
| import-linter layer | `plugins/skills` lives in the existing `plugins` layer; no contract changes | Loader has zero domain dependencies |

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `src/sentinel/plugins/skills/__init__.py` | `SkillHandle`, `_load_all_skills()`, `load_skills_for()`, frontmatter parser, hash, `skill_activated` log emission |
| `src/sentinel/plugins/skills/k8s-crashloop-runbook/SKILL.md` | Seed — `applies_to: ["k8s_*", "kubernetes_crashloop"]` |
| `src/sentinel/plugins/skills/database-connection-runbook/SKILL.md` | Seed — `applies_to: ["database_*", "db_connection"]` |
| `src/sentinel/plugins/skills/latency-spike-runbook/SKILL.md` | Seed — `applies_to: ["latency_*", "performance_*"]` |
| `src/sentinel/plugins/skills/auth-error-response/SKILL.md` | Seed — `applies_to: ["auth_*", "permission_denied"]` |
| `src/sentinel/plugins/skills/rate-limit-response/SKILL.md` | Seed — `applies_to: ["rate_limit_*", "throttling"]` |
| `src/sentinel/plugins/skills/chart-helm-best-practices/SKILL.md` | Seed — `applies_to: ["chart_*", "helm_*"]` |
| `tests/unit/plugins/skills/__init__.py` | Package marker |
| `tests/unit/plugins/skills/test_loader.py` | Frontmatter parsing, hash stability, deterministic order, `max` truncation, glob match, empty-category fallback, `skill_activated` event emission |
| `tests/unit/interfaces/graphs/agents/test_utils.py` | Tests for `append_skills_to_prompt` (extend if exists) |
| `tests/integration/interfaces/mcp/test_list_skills.py` | FastMCP `list_skills` returns sorted seed catalogue |

### Modified files
| File | What changes |
|------|-------------|
| `src/sentinel/interfaces/graphs/agents/utils.py` | Add `append_skills_to_prompt(*, base_prompt, category, max_skills=5) -> str` |
| `src/sentinel/interfaces/graphs/agents/alert_classifier.py` | Wrap `SYSTEM_PROMPT` via `append_skills_to_prompt(category="alert_triage")` |
| `src/sentinel/interfaces/graphs/agents/root_cause_analyser.py` | Use `@agent.system_prompt` to dynamically append based on `Dependencies.category` |
| `src/sentinel/interfaces/graphs/agents/ticket_reviewer.py` | Same dynamic pattern |
| `src/sentinel/interfaces/graphs/agents/response_drafter.py` | Same dynamic pattern |
| `src/sentinel/interfaces/graphs/agents/chart_generator.py` | Static append with category `chart_helm` |
| `src/sentinel/interfaces/mcp/server.py` | Add `@mcp.tool() async def list_skills()` |
| `pyproject.toml` | Verify PyYAML in runtime deps; add if missing |

## Steps

### Phase 1 — Loader and SkillHandle
- [x] **Step 1: Write loader tests** covering `TestSkillHandle`, `TestLoadAllSkills`, `TestLoadSkillsFor` (10+ cases). Use `tmp_path` + monkeypatched `SKILLS_DIR`.
- [x] **Step 2: Run tests — verify they fail**
- [x] **Step 3: Implement `SkillHandle` and loader** in `plugins/skills/__init__.py`
- [x] **Step 4: Run tests — verify pass**
- [x] **Step 5: Commit** — `feat: add skills loader with deterministic ordering and SHA-256 hashing`

### Phase 2 — Seed catalogue
- [x] **Step 6: Add the six seed SKILL.md files** with placeholder bodies and `applies_to` patterns
- [x] **Step 7: Add a regression test** locking the catalogue to exactly 6 seeds
- [x] **Step 8: Run loader tests against real catalogue**
- [x] **Step 9: Commit** — `feat: seed initial Skills catalogue with 6 placeholder runbooks`

### Phase 3 — `append_skills_to_prompt` helper
- [x] **Step 10: Write tests** in `test_utils.py` (5 cases)
- [x] **Step 11: Implement helper** with section format:
  ```
  <base_prompt>

  ---
  ## Applicable Skills
  ### {name} (v{version})
  {body}
  ```
- [x] **Step 12: Run tests — verify pass**
- [x] **Step 13: Commit** — `feat: add append_skills_to_prompt helper for agent system prompts`

### Phase 4 — Wire helper into 5 agents (one commit per agent)
- [x] **Step 14: alert_classifier** (static) — commit: `feat: wire skills loader into alert_classifier agent`
- [x] **Step 15: root_cause_analyser** (dynamic via `@agent.system_prompt`) — commit: `feat: wire skills loader into root_cause_analyser agent`
- [x] **Step 16: ticket_reviewer** (dynamic) — commit
- [x] **Step 17: response_drafter** (dynamic) — commit
- [x] **Step 18: chart_generator** (static `chart_helm`) — commit

### Phase 5 — `list_skills` MCP tool
- [x] **Step 19: Write integration test** using `fastmcp.Client`
- [x] **Step 20: Implement** `@mcp.tool() async def list_skills()`
- [x] **Step 21: Run integration test — verify pass**
- [x] **Step 22: Commit** — `feat: add list_skills tool to FastMCP server`

### Phase 6 — Documentation
- [x] **Step 23: Tick PRD §7 boxes** and note §1/§2 runbook-selection boxes now unblocked
- [x] **Step 24: Commit** — `docs: mark Skills runtime acceptance criteria complete`

### Phase 7 — Config-driven skills refactor (added 2026-04-08)

User feedback: the import-time `append_skills_to_prompt(category=...)` wiring
in each agent module is too rigid. Operators should declare skill-per-agent
mapping in `config.py` (Python code, git-reviewable, test-friendly) and the
Configuration should own agent construction end-to-end. Models move from
`.run(model=...)` to `build_agent(model=...)`. Dynamic runtime `@agent.system_prompt`
injection is kept as a second-layer mechanism on top of configured static skills.

- [x] **Step 25: Foundation** — add `SkillNotFoundError` exception and `compose_system_prompt(*, base_prompt, skill_names)` helper that resolves skill names (not categories) against the installed catalogue and raises loudly on typos. TDD: 5 new tests cover empty pass-through, name resolution, config-order preservation, single-missing, multi-missing. Commit: `feat(skills): add SkillNotFoundError and compose_system_prompt helper`
- [x] **Step 26: Agent factories** — refactor all 8 agent modules (alert_classifier, root_cause_analyser, ticket_reviewer, response_drafter, chart_generator, chart_request_parser, intent_router, k8s_investigator) to expose `build_agent(*, model=None, skills=())`. Keep a module-level `agent = build_agent()` fallback with placeholder `"test"` model for backward compatibility with existing tests that patch `<module>.agent`. Module-level helper functions (`_build_context`, `_inject_runbook_skills`, etc.) are shared across factory invocations via `agent.instructions(fn)` / `agent.system_prompt(fn)` non-decorator syntax. Commit: `refactor(agents): expose build_agent factory on every agent module`
- [x] **Step 27: Configuration.load_agents** — add `SKILLS_BY_AGENT: dict[str, tuple[str, ...]]` at `config.py` module level as the single source of truth for skill-per-agent mapping. Add `Configuration.load_agents()` that iterates every pipeline agent and calls its `build_agent` factory with the normalised model identifier and the configured skills. Add `Configuration.agent_for(name)` returning cached instances, raising `KeyError` on unknown names or before `load_agents()` was called. Wire `get_config()` to call `load_agents()` after `load_vendors()`. 8 new unit tests under `tests/unit/test_config_load_agents.py` covering: every expected agent present, every factory called exactly once, normalised model kwargs passed, configured skills forwarded, cached identity on repeat lookups, unknown-name KeyError, pre-load KeyError, end-to-end SkillNotFoundError propagation from typoed skill name. Commit: `feat(config): add load_agents and agent_for for declarative agent wiring`
- [ ] **Step 28: Graph consumption** — update `sre_investigation.py`, `support_review.py`, `chart_generation.py` to read agents off `cfg.agent_for(name)` instead of importing `<agent_module>.agent` directly. Drop the `classifier_model` / `analyser_model` / `reviewer_model` / `drafter_model` / `chart_parser_model` / `chart_generator_model` fields from each graph's `Dependencies` dataclass (models now live on the pre-built agent). Add a `config: Configuration` field to each graph `Dependencies`. Update every functional test that patches `<module>.agent` or passes `*_model` kwargs. This is the breaking change for the functional test suite. **Not yet executed — awaiting user sign-off on blast radius.**
- [ ] **Step 29: Remove module-level `agent = build_agent()` fallback** from all 8 agent modules once graph consumption has moved over and nothing imports the module-level singleton any more.
- [ ] **Step 30: Remove the import-time `append_skills_to_prompt` / `render_skills_section` wiring** from agent modules (the old category-literal wiring) — these become dead code once `compose_system_prompt` via config owns all static skill composition.
- [ ] **Step 31: Docs** — sync `docs/claude-plan.md` and PRD with the config-driven model; update `Outcome` section here.

## Test Plan

### Unit
- `tests/unit/plugins/skills/test_loader.py` — all loader semantics; mocks `logs.log_event`
- `tests/unit/interfaces/graphs/agents/test_utils.py` — `append_skills_to_prompt` with monkeypatched `load_skills_for`
- Existing per-agent tests get a new assertion that the seed skill header appears in the prompt

### Integration
- `tests/integration/interfaces/mcp/test_list_skills.py` — real seed catalogue through FastMCP `Client`

### What to mock
- `sentinel.utils.logs.log_event`
- `sentinel.plugins.skills.load_skills_for`
- LiteLLM gateway (via existing functional-test conftest)

### What NOT to mock
- The real seed catalogue in the integration test
- `hashlib.sha256` — tests assert stability, not a specific value

## Acceptance Criteria Mapped to PRD §7

| PRD checkbox | Deliverable |
|---|---|
| `plugins/skills/<name>/SKILL.md` layout with frontmatter | Step 6 + Step 3 |
| `load_skills_for(category, max)` deterministically sorted | Step 3 + Step 1 |
| Skills appended via `agents/utils.py` | Steps 10–18 |
| `list_skills` FastMCP tool | Steps 19–22 |
| §1/§2 classifier-driven skill selection | Unblocked by Steps 15–17 |
| §4 `skill activations logged as structlog events` | Partial — event emitted; persistence is audit slice |
| §6 `skill files content-hashed` | `SkillHandle.sha256` (Step 3); persistence is replay slice |

## Risks / Open Questions
1. **Category vocabulary drift** between classifiers. Mitigation: family globs; follow-up proposal for typed `Category` enum.
2. **Static vs dynamic injection** — both used; lru_cache makes per-call load O(1).
3. **Skill body size cap?** No limit today. Defer until prompt-caching slice gives us measurement.
4. **`list_skills` confidentiality** — seed catalogue not secret; flag for security review.
5. **PyYAML dependency** — verify in `pyproject.toml` before Step 1.
6. **Functional-test snapshot collisions** — scan `tests/functional/` for frozen-string prompt assertions before Step 14.

## Changes
| Date | What changed | Why |
|---|---|---|
| 2026-04-08 | Added `render_skills_section` sibling helper alongside `append_skills_to_prompt` | Dynamic-category agents pass a static `system_prompt=` to PydanticAI and then use `@agent.system_prompt` to append run-time content; calling `append_skills_to_prompt(base_prompt=SYSTEM_PROMPT, ...)` from the decorator would duplicate the static base, so a separate helper returning just the Markdown section (or empty string) is required. |
| 2026-04-08 | `ticket_reviewer` wired statically (category `"ticket_triage"`), not dynamically as the plan specified | Plan error: `ticket_reviewer` IS the support classifier — it produces the category rather than consuming one. There is no upstream category to read. Static wiring matches `alert_classifier`'s pattern; dynamic wiring would be nonsensical. |
| 2026-04-08 | `list_skills` test placed under `tests/unit/interfaces/mcp/` rather than `tests/integration/interfaces/mcp/` | The tool has no DB or network dependency — it only reads files via the Skills loader. Integration-tier tests require Postgres fixtures; a unit test against the real seed catalogue is strictly sufficient and avoids pulling in unneeded test infrastructure. |
| 2026-04-08 | Added public `all_installed_skills()` accessor alongside the private `_load_all_skills()` | `list_skills` MCP tool was accessing `_load_all_skills` across a module boundary, which ruff's SLF001 rule rightly rejects. The new thin public wrapper keeps cache implementation private while giving external callers a proper entry point. |
| 2026-04-08 | PyYAML declared as a direct runtime dep (was previously transitive via datadog-api-client / jira), `types-PyYAML` added to dev deps, and the `# type: ignore[import-untyped]` on `domain/charts/policies.py` removed | Plan Risk #5 flagged PyYAML as "not verified"; it was in fact installed transitively. Declaring it explicitly is best practice — `domain/charts/policies.py` already used `import yaml` but was relying on a type-ignore to silence mypy. Declaring `types-PyYAML` makes the ignore obsolete and was removed in the same commit. |
| 2026-04-08 | Added `classification_category` field to SRE graph `State` + threaded through `AnalyseRootCause` → `root_cause_analyser.Dependencies.category` | The plan called for dynamic injection into `root_cause_analyser` via `ctx.deps.category`, but the field did not exist — the classifier output's category was being discarded after the `ClassifyAlert` node finished. Adding the field to `State` is the minimal threading change that keeps the dynamic-injection pattern correct. |

## Outcome

### What was delivered
- Complete Skills runtime package at `src/sentinel/plugins/skills/` with loader, SkillHandle attrs class, frontmatter parser, SHA-256 content hashing, deterministic alphabetical ordering, `fnmatch`-based `applies_to` matching (case-insensitive with empty-list universal semantics), lru-cached disk reads, and `skill_activated` / `skills_no_match` structlog events.
- Six placeholder seed skills: `k8s-crashloop-runbook`, `database-connection-runbook`, `latency-spike-runbook`, `auth-error-response`, `rate-limit-response`, `chart-helm-best-practices`.
- Two helpers in `interfaces/graphs/agents/utils.py`: `append_skills_to_prompt` (static wiring) and `render_skills_section` (dynamic `@agent.system_prompt` wiring).
- All five in-scope agents wired: `alert_classifier`, `root_cause_analyser`, `ticket_reviewer`, `response_drafter`, `chart_generator` (`k8s_investigator` deliberately left alone as the MCP reference implementation).
- `list_skills` FastMCP tool on `interfaces/mcp/server.py` returning a JSON-encoded catalogue list (metadata only — bodies stay off the wire).
- 27 new tests (17 loader tests, 3 seed-catalogue regression tests, 9 helper tests, 3 `list_skills` tests). Full suite: **527 passed, 0 failed**.
- PyYAML declared as a direct dep; spurious `type: ignore` in `domain/charts/policies.py` removed.
- 14 atomic commits with conventional messages on `feature/skills-runtime` branch.

### Follow-up / tech debt
- Real runbook content for the 6 seed skills (separate content PRs, owned by SRE / support / platform teams).
- None of the seed skills have an `applies_to` matching the classifier's `"alert_triage"` / `"ticket_triage"` / `"chart_helm"` literals yet — `alert_classifier`, `ticket_reviewer`, and `chart_generator` wiring is live but inert until such skills are added. This is intentional (the wiring is pay-on-content).
- `Skill body size cap` (plan Risk #3) still deferred — revisit once prompt-caching slice (slice 3) gives us token-cost measurements.
- Typed `Category` enum (plan Risk #1) not addressed — category strings are still free-form snake_case produced by the classifier agents. Family-glob `applies_to` patterns mitigate drift for now.
- `list_skills` confidentiality (plan Risk #4) — seed catalogue is not secret, but a future `fetch_skill` tool (for bodies) should go through auth / tagging review.
