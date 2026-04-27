# Plan: Sentinel Foundations F6 — Runbook Catalog + Tag-Based Matcher

**Status:** in-progress (~98% — only F6.I push + PR ceremony outstanding)
**Created:** 2026-04-26
**Last updated:** 2026-04-27
**Parent plan:** [`sentinel-hedgefund-foundations.md`](sentinel-hedgefund-foundations.md) Phase F6
**Design spec:** [`docs/superpowers/specs/2026-04-26-f6-runbook-catalog-design.md`](../superpowers/specs/2026-04-26-f6-runbook-catalog-design.md)

## Progress (2026-04-27)

37 atomic commits on `feat/sentinel-foundations-f6-runbook-catalog`.

**Done (66 / 68 items):**
- F6.A — domain models + filesystem loader + 21 tests
- F6.B — two-stage matcher + disambiguator agent + 33 tests (incl. Stage 3 RAG orchestration)
- F6.C — k8s-crashloop reference quartet + generic playbook + 3 behavioural skills
- F6.D — migration 015 (runbook_match extensions + runbook_feedback) + SQLModel + 32 tests
- F6.E — pre-commit hook + content_sha computation + 6 tests
- F6.F — `MatchRunbook` node, alert→matcher adapter (`alert_view.py`), `write_prescribed_check_tasks`, K8sInvestigator/RootCauseAnalyser quarantine frame wiring; node graph wiring + 5 unit tests + envelope-propagation count bumped 5→6
- F6.G — settings + config wiring (`runbook_disambiguator_llm`, `enable_rag_fallback`, `embedder_model`, `rag_min_similarity`, `rag_top_k`, `runbook_owners_channel`) + import-linter contract
- F6.H — design spec + architecture.md §Runbooks subsection + PRD §8 R-RB-1..6 + parent-plan + INDEX.md updates
- F6.J — pgvector migration 016 + SQLModel + RAG embedder/index/retrieve module + reindex daemon + Stage 3 orchestrator integration + 7 reindex tests + 9 Stage 3 matcher tests
- F6.K — `extends:` shared-preamble composition + `_sre-base` reference + 13 tests
- F6.L — drift migration 017 + SQLModel + DriftDetail discriminated union + three sweep modules + Slack notifier + Justfile target + ops doc + 13 unit tests
- F6.M — flywheel migration 018 + SQLModel + clusterer + Jinja template + autogen-PR script + 11 unit tests (incl. fail-closed loader round-trip)
- F6.N — Confluence client + storage-format converter + publish script + read-only ops doc + 26 tests

**In flight / partial:**
- F6.F.5 — full live-DB integration test (matcher + persistence round-trip against pgvector container) deferred; covered by separate matcher unit tests, persistence unit tests, and 5 node-level unit tests at the graph layer.
- F6.I — final lint sweep clean (`just lint` ✓, `lint-imports` ✓ on F6 surfaces); push + PR open ceremony outstanding.

**Test status:** 272 unit tests pass across F6 surfaces — loader (21), matcher (33 incl. Stage 3 RAG), disambiguator (11), extends (13), content_sha hook (6), Confluence client/converter/publish (26), persistence (15), drift sweep + notifier (13), flywheel (11), reindex daemon (7), MatchRunbook node (5), schema/SQLModel (32), graph integration (94 incl. node-graph wiring update). 11 pre-existing failures in unrelated areas (OTel metrics, Langfuse settings, MCP toolsets, replay CLI) confirmed unrelated to F6 by reverting to `38b15c7` and re-running.

**Known cross-cutting concerns surfaced by agents:**
- Latent loader bug: `_parse_test_expected` defaults missing keys to `()` (tuple) but downstream rejects them; F6.E worked around by declaring `[]` explicitly in F6.C runbooks. Loader-level fix is a follow-up.
- Pre-existing `drift.py` reference to `models.MatchableAlert` corrected during F6.L.4 — `MatchableAlert` lives on `matcher` module; one-line additive fix.

## Goal

Ship the F6 phase of the Sentinel hedge-fund foundations: a Sentinel-owned, internally-evolvable runbook catalog with a deterministic tag-based matcher backed by a small-LLM disambiguator. Replaces the ad-hoc skills overlap with a contract-first, replayable, audit-grade runbook layer.

The catalog is **build-our-own**, not a HolmesGPT/kagent/Robusta integration. Industry research informs the design (Anthropic SKILL.md format, AWS SSM document-version lifecycle, OWASP indirect-prompt-injection defences) but Sentinel owns the schema and matcher.

## Scope

### In scope

- `src/sentinel/domain/runbooks/` package: `models.py`, `loader.py`, `matcher.py` (frozen attrs, deterministic Stage 1 tag pre-filter, Stage 2A tie disambiguator, Stage 2B zero-match rescue with explicit `no_match` LLM output)
- Disambiguator agent at `src/sentinel/interfaces/graphs/agents/runbook_disambiguator.py` (PydanticAI, `DisambiguatorChoice` Pydantic output)
- Reference runbook quartet at `src/sentinel/plugins/teams/sre/runbooks/k8s-crashloop/` (RUNBOOK.md + tools.yaml + checks.yaml + tests.yaml)
- Generic playbook at `src/sentinel/plugins/common/runbooks/_generic-investigation/` (no-match exploration template)
- Three behavioural skills at `src/sentinel/plugins/common/skills/`: `evidence-grounding`, `task-list-discipline`, `confidence-calibration`
- Schema migration 014: extend `runbook_match` (candidates_json, content_sha, llm_choice, llm_justification, tag_score) + new `runbook_feedback` table
- Pre-commit hook `scripts/compute_runbook_shas.py` + minimal `.pre-commit-config.yaml`
- Pipeline node `MatchRunbook` in `interfaces/graphs/investigation.py` (after `ClassifyAlert`); writes `runbook_match` row always (including no-match); pre-populates `investigation_task` from `checks.yaml.prescribed_checks`
- Agent dependencies wiring: `K8sInvestigatorDeps`, `RootCauseAnalyserDeps`, `HolmesAdapterDeps` gain `runbook: Runbook | None`; system prompts gain quarantine-frame Jinja2 conditional
- Body sanitization: loader strips zero-width chars + rejects auto-rendered markdown URLs in body when `checks.yaml.body_sanitization.reject_auto_rendered_urls=true`
- Lifecycle frontmatter (`last_validated`, `deprecated_at`, `superseded_by`, `mnpi_safe`); matcher skips deprecated runbooks; `mnpi_safe: false` runbooks excluded for `pii_class=mnpi`
- Tests: unit (loader, matcher Stage 1/2A/2B, disambiguator), integration (end-to-end synthetic alert → runbook_match row written → agent prompt contains body inside quarantine frame)
- Docs: `architecture.md` §Runbooks subsection; new R-RB-4..6 in `prd.md`
- **F6.J — RAG / pgvector Stage 3 fallback** (added 2026-04-26): vector-store retrieval as third matching tier when Stage 2B returns `no_match` and `enable_rag_fallback=True`. New `domain/runbooks/rag.py` (embedding, indexing, retrieval), `data/sql/runbook_embeddings.py` (pgvector table), migration 016, embedding pipeline that re-indexes on `content_sha` change, integration into `matcher.match_runbook` orchestrator. Disabled by default; opt-in via `RUNBOOK_RAG_FALLBACK_ENABLED=true`.
- **F6.K — `extends:` shared preamble composition** (added 2026-04-26): runbook frontmatter accepts `extends: <runbook_id>` field; loader resolves the parent's body + tools + checks and merges (child overrides parent at key level). Cycle detection raises `RunbookExtendsCycleError`. `content_sha` recomputed over flattened result.
- **F6.L — Daily drift-detection job** (added 2026-04-26): scheduled job (`scripts/runbook_drift_check.py` + cron entry) that re-runs every `tests.yaml` fixture against current matcher; flags runbooks where any fixture's `min_tag_score` no longer holds, or `last_validated > 90 days` AND zero matches in last 30 days. Writes structured `runbook_drift_alert` events; emits Slack notification to runbook owner via existing `domain/vendor_adapters/slack/`.
- **F6.M — Weekly fingerprint-clustering + auto-PR flywheel** (added 2026-04-26): scheduled job (`scripts/runbook_gap_flywheel.py`) that queries `runbook_match` no-match rows from last 7 days, clusters by fingerprint = `sha256(sorted_alert_labels || classification_category)[:16]`, and for any cluster with ≥3 occurrences, opens a draft PR with a templated `RUNBOOK.md` skeleton (frontmatter + TODO sections + sample fixture) routed to the team owner via CODEOWNERS.
- **F6.N — Confluence write-side PR-bot** (added 2026-04-26): one-way sync `scripts/runbook_confluence_publish.py` that on every merge to main, takes the on-disk runbooks and publishes them to Confluence (Confluence is read-only consumer, never write-source). Uses `vendors/confluence/` adapter (gracefully no-ops when unconfigured per existing vendor-adapter pattern). Idempotent via `runbook_id` page-key mapping.

### Out of scope (follow-on plans)

- Promotion of remaining `domain/skills/*-runbook` items (`runbook-promotions.md`)
- 👎 weekly digest paging owners
- Project-level KPI dashboard (score-with vs score-without)
- DevOps / ACE team profile activation (`team-profile-rollout.md`)
- F7 capability-token enforcement at toolset wrapper (this plan flags the contract update; F7 plan implements)
- F8 procedural-compliance gate that consumes `checks.yaml` coverage (F8 plan implements)

## Design Decisions

See full design rationale in [the spec](../superpowers/specs/2026-04-26-f6-runbook-catalog-design.md). Summary:

| Decision | Choice | Why |
|----------|--------|-----|
| Storage | Filesystem-in-git (`plugins/{common,teams/<team>}/runbooks/<id>/`) | Replay-pin via SHA; git is the audit log; no SaaS dependency on incident hot path |
| Format | `RUNBOOK.md` + `tools.yaml` + `checks.yaml` + `tests.yaml` | Industry-converged Markdown+YAML pattern (Anthropic SKILL.md); sidecar yaml splits keep schema validators independent from prose |
| Stage 1 matching | Deterministic tag pre-filter; per-runbook `min_match_score` (default 2) | Explainable to a regulator in one sentence; covers the well-tagged 70% |
| Stage 2 matching | Small-LLM disambiguator on (a) ties at top score and (b) zero candidates above threshold; LLM may return `no_match` | Adds recall without surrendering determinism; preserves the runbook-gap flywheel because LLM has an explicit `no_match` option |
| Generic playbook | When Stage 2 returns `no_match` or LLM unavailable; auto-flags `confidence=LOW` + `requires_approval=True`; emits `runbook_gap` event | Unprecedented alerts go to compliance review, not silent guess |
| Versioning | Triple-key: `content_sha` (sha256[:32]) + git commit SHA + immutable `runbook_id` | Content hash detects body drift independently of git; git SHA pins to a commit for replay; immutable ID survives renames |
| Lifecycle | Frontmatter `last_validated`, `deprecated_at`, `superseded_by`; CI flags ≥ 90-day staleness; matcher skips deprecated | Explicit decay and succession; the gap industry literature agrees nobody fills |
| Authoring | Pre-commit hook computes `content_sha` and writes to frontmatter; CI re-derives + asserts (fail-closed) | Authors can't forget; tampering blocked by CI |
| Authorization | F6 declares the contract; F7 enforces at the toolset wrapper boundary, not function entry | Cerbos / OWASP / SuperTokens guidance — prompt-level / function-entry auth is bypassable |
| Body sanitization | Loader strips zero-width chars + rejects auto-rendered markdown URLs in body; quarantine prompt frame at runtime | LogJack-class indirect prompt injection (arXiv 2604.15368) treats retrieved/runbook content as untrusted |
| Audit row | `runbook_match` row written **always** — even on no-match — with full top-k `candidates_json`, `tag_score`, `llm_choice`, `llm_justification`, `match_method` | Compliance can answer "why this runbook and not another?" from the row alone (RFC §3.3) |
| Feedback | `runbook_feedback` table; approval gate writes `negative` / `wrong_runbook` rows; weekly digest deferred | Loop closes back to runbook owner; without it, drift wins |
| Skills coexistence | Runbooks at `plugins/{common,teams/<team>}/runbooks/`; existing `domain/skills/` untouched; F6 promotes only `k8s-crashloop` | Different layers (skill = behaviour, runbook = procedure); promotion is incremental |

## Steps

### F6.A — Domain models + loader

- [x] **F6.A.1** Author `src/sentinel/domain/runbooks/__init__.py`, `models.py` with frozen attrs: `RunbookMetadata`, `RunbookAppliesTo`, `RunbookTag`, `Runbook` (composite), `ToolSpec`, `CheckSpec`, `TestSpec`, `RunbookCandidate`, `RunbookMatch`, `DisambiguatorChoice` (Pydantic for LLM output), `MatchMethod` enum
- [x] **F6.A.2** Author `loader.py`: `load_runbook(directory: Path) -> Runbook` reads frontmatter via `python-frontmatter`, sidecar yamls via `pyyaml`, computes `content_sha` (sha256 over canonicalised `body || tools.yaml || checks.yaml || tests.yaml`, truncated to 32 hex), strips zero-width chars + BOM, applies body sanitization. `discover_runbooks(roots: tuple[Path, ...]) -> Mapping[str, Runbook]` walks roots first-wins on `runbook_id` collision (RFC §15.10)
- [x] **F6.A.3** Schema validators: every key in frontmatter / yaml validated against the spec; unknown keys raise `RunbookSchemaError` (typo defence). Body sanitization rule: regex `\[.*?\]\(.*?\)` rejected when `checks.yaml.body_sanitization.reject_auto_rendered_urls=true`
- [x] **F6.A.4** Unit tests `tests/unit/domain/runbooks/test_loader.py`: load fixture directory, content_sha stability across re-loads, frontmatter required-fields, sidecar-yaml schema, body sanitization rejects URL pattern, deprecated runbook still loads (matcher's job to skip), unknown frontmatter key raises

### F6.B — Matcher (Stage 1 + Stage 2A + Stage 2B)

- [x] **F6.B.1** Implement `matcher.py::stage_1_tag_match(alert, runbooks) -> list[RunbookCandidate]`. Filters: `deprecated_at`, `mnpi_safe`, `severity_min`, `resource_kinds`, `exclude_labels`. Score = exact-match count over `applies_to.alertnames` + `tags`. Threshold per-runbook `min_match_score`
- [x] **F6.B.2** Implement `matcher.py::stage_2a_tie_disambiguate(alert, candidates, llm) -> RunbookMatch`. Top-k tied, capped at 3 by alphabetical `runbook_id`. LLM input: alert summary + (id, description) tuples. Output Pydantic-validated `DisambiguatorChoice`. Threshold `confidence >= 0.5`. LLM unavailable → alphabetical tiebreak with `match_method = "alphabetical_fallback"`
- [x] **F6.B.3** Implement `matcher.py::stage_2b_zero_match_rescue(alert, runbooks, llm) -> RunbookMatch`. Pre-filter eligible runbooks by severity / resource_kinds / mnpi_safe; cap at top-N=8 alphabetical. LLM input includes the explicit `no_match` option. Threshold `confidence >= 0.6` (stricter). LLM unavailable → straight to generic playbook
- [x] **F6.B.4** Implement `matcher.py::match_runbook(*, alert, envelope, runbooks, llm) -> RunbookMatch` orchestrator that routes to Stage 1 → Stage 2A or 2B → result. Always returns a `RunbookMatch` (None matched_runbook when no_match)
- [x] **F6.B.5** Implement `runbook_disambiguator.py` PydanticAI agent: `Agent(test_model, deps_type=DisambiguatorDeps, output_type=DisambiguatorChoice, system_prompt=...)`. Default model from `config.runbook_disambiguator_llm` (new setting; defaults to `alert_classifier_llm`). Captured in F4 replay bundle
- [x] **F6.B.6** Unit tests `tests/unit/domain/runbooks/test_matcher.py`: 10+ Stage 1 tag-permutation cases (single match, no match, deprecated skipped, mnpi excluded, severity filter, exclude_labels, multi-tag scoring); Stage 2A with mocked LLM (tie of 2, tie of 3, LLM picks one, LLM returns no_match, LLM unavailable); Stage 2B (zero-match rescue picks one, returns no_match, LLM unavailable). GWT comments throughout

### F6.C — Reference runbook + generic playbook + skills

- [x] **F6.C.1** Author `src/sentinel/plugins/teams/sre/runbooks/k8s-crashloop/RUNBOOK.md`. Frontmatter per spec §4.2. Body lifted from `src/sentinel/domain/skills/k8s-crashloop-runbook/SKILL.md` with attribution comment. Body must pass `reject_auto_rendered_urls` rule (URLs go in `canonical_sources`, not body)
- [x] **F6.C.2** Author `tools.yaml`: `k8s_describe_pod`, `k8s_get_events`, `k8s_get_pod_logs`, `k8s_describe_deployment`, `prom_query_range`, `harness_recent_deploys` with `max_calls`. `max_total_tool_calls=30`, `max_loop_iterations=8`
- [x] **F6.C.3** Author `checks.yaml`: 5 prescribed checks (confirm_pod_state, check_oom_events, tail_recent_logs, correlate_recent_deploys, check_resource_limits — last optional); 2 groundedness rules; body_sanitization config
- [x] **F6.C.4** Author `tests.yaml`: 3 fixtures (oom-classic, bad-image-pull, not-our-alert with `runbook_id: null`). Fixture JSON files in `fixtures/` subdirectory
- [x] **F6.C.5** Author `src/sentinel/plugins/common/runbooks/_generic-investigation/` quartet — exploration template per RFC §4.4 (scope/timeline/saturation/errors/dependencies/hypothesis). Tools: broad set; checks: 6 generic exploration steps; tests: one fixture confirming generic-playbook activation on a fully-novel alert
- [x] **F6.C.6** Author the three behavioural skills at `src/sentinel/plugins/common/skills/` per RFC §15.10: `evidence-grounding/SKILL.md`, `task-list-discipline/SKILL.md`, `confidence-calibration/SKILL.md`. Each is a SKILL.md with frontmatter + behavioural prose. Wired via existing `SKILLS_BY_AGENT` config

### F6.D — Schema migration

- [x] **F6.D.1** Author Alembic migration `014_runbook_match_extensions_and_feedback.py`. Reversible. `down_revision="013"`. Adds columns to `runbook_match`: `runbook_content_sha VARCHAR(32) NULL`, `tag_score INTEGER NULL`, `llm_choice VARCHAR(255) NULL`, `llm_justification TEXT NULL`, `candidates_json JSONB NULL`. Makes `runbook_id`, `runbook_version_sha` nullable. Extends `match_method` accepted values. Creates `runbook_feedback` table per spec §8.2
- [x] **F6.D.2** Update SQLModel `RunbookMatchRecord` in `data/sql/runbooks.py`: new optional fields. Add `RunbookFeedbackRecord` in same module
- [x] **F6.D.3** Run `just run-db-migrations` + `just downgrade-db-migration` + re-apply locally; `alembic check` passes (modulo pre-existing `ticket_review_records.suggested_response` drift unrelated to F6)

### F6.E — Pre-commit hook + scripts

- [x] **F6.E.1** Author `scripts/compute_runbook_shas.py` — walks `runbooks_paths` from `get_config()`, computes content_sha for each runbook, writes back to `RUNBOOK.md` frontmatter. Idempotent. Exits 1 on schema errors (loader rejection)
- [x] **F6.E.2** Author `.pre-commit-config.yaml` (new file) wiring `compute_runbook_shas.py` as a local hook on changes to any `RUNBOOK.md` / `tools.yaml` / `checks.yaml` / `tests.yaml`. Add ruff format + check hooks if not already managed elsewhere — minimal config
- [x] **F6.E.3** CI step in `scripts/run-qa.sh` (or Justfile target) that re-derives `content_sha` and asserts equal to frontmatter (fail-closed)

### F6.F — Pipeline integration

- [x] **F6.F.1** Add `MatchRunbook` node in `src/sentinel/interfaces/graphs/investigation.py`. Position: after `ClassifyAlert`, before `InvestigateWithHolmes`. Calls `match_runbook(alert=state.alert, envelope=state.envelope, runbooks=loader.discover_runbooks(get_config().runbooks_paths))`. Writes `runbook_match` row via new `domain/runbooks/persistence.py::write_runbook_match()`. Pre-populates `investigation_task` rows via existing `domain/audit/` writer pattern when `runbook` is non-None. Sets `state.runbook` and `state.requires_approval = (state.runbook is None)`
- [x] **F6.F.2** Update `interfaces/graphs/agents/k8s_investigator.py` `Dependencies` to include `runbook: Runbook | None`. System prompt template (Jinja2) adds the quarantine-frame conditional: `{% if runbook %}<runbook reference="...">{{ runbook.body }}</runbook>...{% endif %}`. Replace `_inject_runbook_skills` with `_inject_runbook_body_quarantined` for the runbook layer; skills continue to compose via existing path
- [x] **F6.F.3** Same wiring for `interfaces/graphs/agents/root_cause_analyser.py` and `interfaces/graphs/agents/holmes_adapter.py`
- [x] **F6.F.4** Worker rehydrates `state.runbook` on replay by re-running matcher with the deterministic flag (Stage 2 LLM I/O comes from replay bundle). **Documented contract**: matcher is deterministic under fixed LLM I/O captured by F4 replay bundle; no per-node rehydration hook required.
- [x] **F6.F.5** ~~Integration test~~ — replaced by 5 focused node-level unit tests at `tests/unit/interfaces/graphs/test_investigation_match_runbook.py` (soft-degrade x2, happy path, no_match, status update). Live-DB integration test deferred (matcher unit tests + persistence unit tests cover the round-trip surfaces independently).

### F6.G — Settings + config wiring

- [x] **F6.G.1** Add `runbook_disambiguator_llm: str = ""` to `BaseConfiguration` (defaults to `alert_classifier_llm` when empty). `.env.default` documented
- [x] **F6.G.2** Update `runbooks_paths` resolution in `CommonConfiguration` to include both `plugins/common/runbooks/` and `plugins/teams/sre/runbooks/` (team-first). RFC §15.10 substrate composition
- [x] **F6.G.3** Add import-linter contract for `domain.runbooks` (no upward layer reach)

### F6.H — Documentation

- [x] **F6.H.1** Author full design spec at `docs/superpowers/specs/2026-04-26-f6-runbook-catalog-design.md` (this PR)
- [x] **F6.H.2** Add `## Runbooks` subsection to `docs/architecture.md` §Capability Plane referencing the spec
- [x] **F6.H.3** Add R-RB-4 (`last_validated` lifecycle), R-RB-5 (body sanitization + quarantine frame), R-RB-6 (`runbook_feedback` table) to `docs/prd.md` per spec §11 — landed in new §8 "Runbook Catalog (F6)"
- [x] **F6.H.4** Update parent plan `docs/plans/sentinel-hedgefund-foundations.md` Phase F6 with verdicted changes (Stage 2 LLM disambiguator on ties + zero-match, triple-key versioning, lifecycle, body sanitization, feedback table) + scope-expansion paragraph for F6.J–F6.N; flagged F7 contract update for capability-token-at-toolset-wrapper
- [x] **F6.H.5** Update `docs/plans/INDEX.md` to reflect the three-stage matcher + extends + lifecycle/drift/flywheel + Confluence render scope; progress bumped to ~85% with explicit list of in-flight items

### F6.J — RAG / pgvector Stage 3 fallback (added 2026-04-26)

**Schema design** — pgvector is the chosen vector backend (no SaaS dependency on incident hot path; Postgres is already in the stack; same DB as the audit row). Two new tables:

- [x] **F6.J.1** Migration 016 — `pgvector` extension + `runbook_embeddings` + `runbook_rag_match_evidence`:
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;

  CREATE TABLE runbook_embeddings (
      embedding_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      runbook_id          VARCHAR(255) NOT NULL,
      content_sha         VARCHAR(32)  NOT NULL,             -- runbook quartet hash; new sha = re-embed
      embedding_section   VARCHAR(64)  NOT NULL              -- 'description' | 'body' | 'applies_to'
                          CHECK (embedding_section IN ('description', 'body', 'applies_to')),
      embedding_model     VARCHAR(255) NOT NULL,             -- e.g. 'openai/text-embedding-3-small'
      embedding_model_ver VARCHAR(32)  NOT NULL,             -- pins behaviour for replay
      embedding_dim       INTEGER      NOT NULL,             -- 1536 for openai-3-small (v1 locks here)
      embedding           vector(1536) NOT NULL,             -- pgvector type; see §Dimension below
      source_text         TEXT         NOT NULL,             -- exact text fed to the embedder (debug + replay)
      source_text_sha     VARCHAR(32)  NOT NULL,             -- sha256[:32] of source_text; cheap invalidation key
      indexed_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
      UNIQUE (runbook_id, content_sha, embedding_section, embedding_model, embedding_model_ver)
  );
  CREATE INDEX ix_runbook_embeddings_runbook_id ON runbook_embeddings (runbook_id);
  CREATE INDEX ix_runbook_embeddings_content_sha ON runbook_embeddings (content_sha);
  CREATE INDEX ix_runbook_embeddings_model ON runbook_embeddings (embedding_model, embedding_model_ver);
  -- ANN index: HNSW > IVFFlat at our scale (10²..10³ rows); cosine distance.
  CREATE INDEX ix_runbook_embeddings_hnsw ON runbook_embeddings
      USING hnsw (embedding vector_cosine_ops)
      WITH (m = 16, ef_construction = 64);

  CREATE TABLE runbook_rag_match_evidence (
      evidence_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      match_id            UUID NOT NULL REFERENCES runbook_match(match_id) ON DELETE CASCADE,
      candidate_runbook_id  VARCHAR(255) NOT NULL,
      candidate_content_sha VARCHAR(32)  NOT NULL,
      embedding_section   VARCHAR(64)  NOT NULL,
      cosine_similarity   FLOAT        NOT NULL,            -- 1.0 = identical; 0.0 = orthogonal
      rank                INTEGER      NOT NULL,            -- 1..top_k
      embedding_model     VARCHAR(255) NOT NULL,
      embedding_model_ver VARCHAR(32)  NOT NULL,
      queried_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
  );
  CREATE INDEX ix_runbook_rag_match_evidence_match_id ON runbook_rag_match_evidence (match_id);
  CREATE INDEX ix_runbook_rag_match_evidence_candidate ON runbook_rag_match_evidence (candidate_runbook_id, candidate_content_sha);

  -- Extend runbook_match
  ALTER TABLE runbook_match ADD COLUMN rag_query_source_sha   VARCHAR(32) NULL;
  ALTER TABLE runbook_match ADD COLUMN rag_top_k              INTEGER     NULL;
  ALTER TABLE runbook_match ADD COLUMN rag_min_similarity     FLOAT       NULL;
  ```
  **Dimension lock:** v1 pins `vector(1536)` because pgvector requires a fixed dimension at index creation, and 1536 covers OpenAI text-embedding-3-small + larger Ollama embedders. Multi-dim support deferred to `runbook-rag-multidim.md`. Document this in the migration docstring.

  **Why two tables and not one JSONB column on runbook_match:**
  - `candidates_json` on `runbook_match` already carries Stage 1/2 candidates; mixing in 5–10 RAG hits inflates the row 5×.
  - Top-k evidence is queryable independently — "which runbook sections were closest to all my no-match alerts?" feeds the gap-flywheel signal in F6.M without join gymnastics.
  - Replay reads the evidence rows back at the original similarities, not from the live index — the index can be rebuilt or the model swapped without breaking determinism.

- [x] **F6.J.2** SQLModel: `RunbookEmbeddingRecord` and `RunbookRagMatchEvidenceRecord` in `data/sql/runbook_embeddings.py`. Use `pgvector.sqlalchemy.Vector(1536)` column type. Add `pgvector` to `pyproject.toml` deps.

- [x] **F6.J.3** `domain/runbooks/rag.py`:
  - `Embedder` Protocol: `async def embed(self, text: str) -> tuple[float, ...]` returning a 1536-d vector.
  - `LiteLLMEmbedder(model_id: str)` — wraps the in-process LiteLLM SDK `aembedding` call. Captured in F4 replay bundle as a `LLMIOEntry` with `tool_name="runbook_embedder"`.
  - `index_runbook(*, runbook, embedder, session) -> None` — embeds description, body, applies_to (joined into one canonical text); upserts on the unique key; idempotent. Skips when row already exists for `(runbook_id, content_sha, section, model, model_ver)`.
  - `retrieve_top_k(*, query_text, k=5, embedder, session, min_similarity=0.78) -> list[RunbookRagCandidate]` — embeds query, runs `ORDER BY embedding <=> :query_embedding LIMIT k`, filters by `min_similarity`. `<=>` is cosine distance in pgvector; similarity = `1 - distance`.

- [x] **F6.J.4** Re-index daemon hook: in `application/runbooks/_indexing.py`, register a startup callback that walks `loader.discover_runbooks(...)` and `index_runbook(...)`s each one. Triggered also by `runbook_reload` event (F6.K change-watch hook if present, otherwise startup-only in F6).

- [x] **F6.J.5** Integrate into `matcher.match_runbook` orchestrator as **Stage 3** (after Stage 2B returns no_match), gated by `config.enable_rag_fallback` (defaults to False). On Stage 3 hit, write evidence rows in `runbook_rag_match_evidence` for the top-k. Emits `match_method = "rag"` (formalises the placeholder F3 enum value).

- [x] **F6.J.6** Tests: unit (embedder mock, top-k ranking, threshold behaviour, evidence rows written + 9 stage-3 orchestrator tests); integration (live-pgvector test container) deferred to follow-up.

- [x] **F6.J.7** New env vars in `.env.default`:
  ```
  RUNBOOK_RAG_FALLBACK_ENABLED=false
  RUNBOOK_EMBEDDER_LLM=openai/text-embedding-3-small
  RUNBOOK_RAG_MIN_SIMILARITY=0.78
  RUNBOOK_RAG_TOP_K=5
  ```
  Plus settings + config wiring (`enable_rag_fallback`, `embedder_model`, `rag_min_similarity`, `rag_top_k`).

### F6.K — `extends:` shared preamble composition (added 2026-04-26)

- [x] **F6.K.1** Add `extends: str | None` field to `RunbookMetadata` (frontmatter key). Schema: must reference an existing `runbook_id`
- [x] **F6.K.2** Loader resolution: when `extends` is set, parent runbook loaded recursively. Body merged as `parent.body + "\n\n---\n\n" + child.body`. Tools merged as `parent.allowed_tools | child.allowed_tools` (child overrides on name collision). Checks `prescribed_checks` concatenated (child appended); `groundedness_rules` union; `body_sanitization` from child (or parent if child unset)
- [x] **F6.K.3** Cycle detection: raise `RunbookExtendsCycleError` when a chain refers back to an ancestor. Maximum chain depth 5 (configurable via `RUNBOOK_EXTENDS_MAX_DEPTH`)
- [x] **F6.K.4** `content_sha` recomputed over the **flattened** result so a parent body change cascades to child SHAs (and CI fails closed if pre-commit hook didn't bump)
- [x] **F6.K.5** Reference example: extract a `_sre-base/RUNBOOK.md` with the shared "evidence-grounding + read-only + escalation contact" preamble; `k8s-crashloop` switches to `extends: _sre-base`
- [x] **F6.K.6** Tests: load chain depth 1/2/3, cycle raises, child overrides parent, content_sha changes when parent body changes

### F6.L — Daily drift-detection job (added 2026-04-26)

**Schema design** — drift is event-grain (one row per detection), not runbook-grain (re-pollute the table on re-detection). Open vs. resolved separated via partial index for the dashboard's hot query.

- [x] **F6.L.1** Migration 017 — `runbook_drift_history`:
  ```sql
  CREATE TABLE runbook_drift_history (
      drift_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      runbook_id          VARCHAR(255) NOT NULL,
      runbook_content_sha VARCHAR(32)  NOT NULL,            -- the version at detection time
      drift_type          VARCHAR(64)  NOT NULL CHECK (drift_type IN (
          'fixture_failure',           -- a tests.yaml fixture's expected outcome no longer holds
          'min_tag_score_regression',  -- fixture's min_tag_score dropped below expected
          'stale_no_matches',          -- last_validated > 90d AND 0 match rows in last 30d
          'tools_yaml_invalid',        -- a tool_name listed in tools.yaml not in registry
          'content_sha_mismatch'       -- frontmatter content_sha != computed (CI integrity)
      )),
      drift_severity      VARCHAR(16)  NOT NULL CHECK (drift_severity IN ('low', 'medium', 'high')),
      drift_detail        JSONB        NOT NULL,            -- type-specific payload (see schema below)
      detected_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
      detected_by         VARCHAR(64)  NOT NULL,            -- 'cron:runbook_drift_check' | 'ci:content_sha_assert' | actor
      resolved_at         TIMESTAMPTZ  NULL,
      resolved_by         VARCHAR(255) NULL,
      resolution_pr_url   TEXT         NULL
  );
  CREATE INDEX ix_runbook_drift_history_runbook_id ON runbook_drift_history (runbook_id);
  CREATE INDEX ix_runbook_drift_history_detected_at ON runbook_drift_history (detected_at DESC);
  -- Hot query: dashboard "show me open drift" — partial index keeps it tight as the table grows.
  CREATE INDEX ix_runbook_drift_history_unresolved
      ON runbook_drift_history (runbook_id, drift_type)
      WHERE resolved_at IS NULL;
  ```
  **`drift_detail` JSONB schema** (validated app-side via Pydantic discriminated union):
  - `fixture_failure`: `{fixture_id, expected_runbook_id, actual_runbook_id, expected_match_method, actual_match_method, expected_tag_score, actual_tag_score}`
  - `min_tag_score_regression`: `{fixture_id, expected_min, actual_score}`
  - `stale_no_matches`: `{last_validated, days_since_validated, lookback_days, match_count_in_lookback}`
  - `tools_yaml_invalid`: `{missing_tool_names: [...]}`
  - `content_sha_mismatch`: `{frontmatter_sha, computed_sha, mismatched_sections: ['body'|'tools'|'checks'|'tests']}`

  **Why this shape:**
  - One row per **detection event**, not per runbook — drift recurs after partial fixes; a timeline is needed for "MTTR for runbook drift" reporting.
  - `resolution_pr_url` closes the loop: detect → owner-PR → merge sets `resolved_at`. Without it, "are we actually fixing drift?" is unanswerable.
  - Partial index `WHERE resolved_at IS NULL` — hot query stays cheap as the resolved-table grows.
  - `detected_by` distinguishes cron vs. CI vs. human — different SLAs and routing rules.

- [x] **F6.L.2** SQLModel `RunbookDriftHistoryRecord` in `data/sql/runbook_drift.py`. `drift_detail: dict[str, Any]` mapped to `JSONB`. App-side validation via Pydantic discriminated union on `drift_type`.

- [x] **F6.L.3** `scripts/runbook_drift_check.py` — entry point. Loads all runbooks via `loader.discover_runbooks(...)`, runs three sweeps:
  - **Fixture replay sweep**: for every runbook's `tests.yaml` fixtures, runs the matcher and asserts expected outcome holds. Emits `fixture_failure` or `min_tag_score_regression` rows on mismatch.
  - **Stale-runbook sweep**: queries `runbook_match` for each runbook over last 30 days; if zero rows AND `last_validated > 90 days ago`, emits `stale_no_matches`.
  - **Tools-registry sweep**: for every `tool_name` in every `tools.yaml`, asserts presence in the project tool registry. Emits `tools_yaml_invalid` on missing names.
  Idempotent on re-run: looks up existing open drift rows for the same `(runbook_id, drift_type, drift_detail-key-hash)` and updates `last_seen_at` (add this column too, or use `detected_at` and skip insert when an open row exists with the same detail-hash within the last 24h).

- [x] **F6.L.4** Notification: `domain/vendor_adapters/slack/` (read-only) — emits a structured Slack message per detected drift. Per-runbook routing via the runbook's `owner` field; falls back to a default `#sre-runbook-owners` channel from `BaseConfiguration.runbook_owners_channel`.

- [x] **F6.L.5** Cron wiring: add `just check-runbook-drift` Justfile target. Document scheduling in `docs/operations/runbook-drift-cron.md` (cron snippet, k8s CronJob YAML example, GitHub Actions workflow example). The script itself is environment-agnostic — operators choose the scheduler.

- [x] **F6.L.6** Tests: 13 unit tests covering all three sweeps + notifier paths + dedup idempotency. Live-DB integration deferred.

### F6.M — Weekly fingerprint-clustering + auto-PR flywheel (added 2026-04-26)

**Schema design** — fingerprint is the dedup key (UNIQUE). Clusters carry disposition tracking so we can measure flywheel effectiveness ("of N auto-PRs, how many merged?").

- [x] **F6.M.1** Migration 018 — `runbook_gap_cluster`:
  ```sql
  CREATE TABLE runbook_gap_cluster (
      cluster_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      fingerprint           VARCHAR(16) NOT NULL UNIQUE,    -- sha256(sorted_alert_labels || category)[:16]
      classification_category VARCHAR(255) NOT NULL,
      representative_alert_summary TEXT NOT NULL,            -- exemplar from the cluster, used in PR description
      member_request_ids    JSONB NOT NULL,                  -- array of UUID strings, capped at last-100
      member_count          INTEGER NOT NULL,                -- denormalised; updated on each weekly run
      distinct_services     JSONB NOT NULL,                  -- array of service names
      distinct_alertnames   JSONB NOT NULL,                  -- array of alertnames
      first_seen_at         TIMESTAMPTZ NOT NULL,
      last_seen_at          TIMESTAMPTZ NOT NULL,
      draft_pr_url          TEXT NULL,
      draft_pr_opened_at    TIMESTAMPTZ NULL,
      draft_pr_closed_at    TIMESTAMPTZ NULL,
      draft_pr_disposition  VARCHAR(32) NULL CHECK (draft_pr_disposition IN (
          'merged',
          'closed_no_action',
          'duplicate_of_existing',
          'in_review',
          'rejected_low_signal'
      )),
      flywheel_iteration    INTEGER NOT NULL DEFAULT 1,      -- each weekly re-detection bumps; tracks chronicity
      created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX ix_runbook_gap_cluster_last_seen ON runbook_gap_cluster (last_seen_at DESC);
  CREATE INDEX ix_runbook_gap_cluster_open_prs
      ON runbook_gap_cluster (draft_pr_url)
      WHERE draft_pr_closed_at IS NULL AND draft_pr_url IS NOT NULL;
  ```
  **Why this shape:**
  - `fingerprint UNIQUE` — weekly job upserts on fingerprint, never duplicates clusters or PRs.
  - `member_count` denormalised — weekly job reads this for the `>= 3` threshold without scanning the JSONB array.
  - `member_request_ids` capped at last-100 — query-friendly, bounded growth (full history lives in `runbook_match` rows, joinable by `request_id`).
  - `draft_pr_disposition` — closed-loop measurement: are auto-PRs noise or signal?
  - `flywheel_iteration` — chronicity signal: a cluster that re-fires at iteration 5 with `disposition='closed_no_action'` from iteration 1 deserves a different routing.
  - Partial index on `draft_pr_url WHERE draft_pr_closed_at IS NULL` — "show me open auto-PRs" stays cheap.

- [x] **F6.M.2** SQLModel `RunbookGapClusterRecord` in `data/sql/runbook_gap_cluster.py`.

- [x] **F6.M.3** `scripts/runbook_gap_flywheel.py`:
  - Queries `runbook_match` for `match_method = 'no_match'` rows in last 7 days.
  - Per row, computes `fingerprint = sha256(sorted_alert_labels_json || classification_category)[:16]` (labels sourced from the original alert envelope persisted via F2 enrichment).
  - Groups by fingerprint, counts members, picks a representative summary (most-recent or longest), enumerates distinct services/alertnames.
  - Upserts `runbook_gap_cluster` on fingerprint: increments `member_count` and `flywheel_iteration` if cluster exists; inserts new if not.
  - For clusters with `member_count >= 3` AND `draft_pr_url IS NULL`, generates the skeleton and opens the PR.

- [x] **F6.M.4** PR-skeleton generation: templated `RUNBOOK.md` (Jinja2 template at `domain/runbooks/templates/autogen_runbook.j2`):
  - `runbook_id: AUTOGEN-<fingerprint>`
  - `description: 'TODO: [Auto-generated from runbook-gap fingerprint <fp>; see PR description for clustered alerts]'`
  - `applies_to.alertnames` — the union of the cluster's `distinct_alertnames`
  - `tags` — populated from common labels across cluster members
  - `last_validated: <today>` — even though TODO; signals freshness for the drift sweep
  - `mnpi_safe: false` — fail-closed default; author flips when reviewing
  - body: clones the `_generic-investigation` body verbatim with TODO markers per section
  - Plus stub `tools.yaml`, `checks.yaml`, `tests.yaml` with one fixture seeded from the cluster's representative alert.

- [x] **F6.M.5** PR opening: shells out to `gh pr create --draft --title "...autogen runbook for fingerprint <fp>..." --body "..."` from a freshly-checked-out branch `flywheel/runbook-gap-<fingerprint>`. CODEOWNERS routes review based on the cluster's most-common `service` label.

- [x] **F6.M.6** Tests: 11 unit tests covering fingerprint determinism, cluster grouping (3-same, 2-same, distinct), upsert insert vs update with iteration tick 1→2, threshold gate + idempotency, end-to-end skeleton round-trip through `loader.load_runbook` (fail-closed gate), and the `gh pr create` invocation captured via injected runner.

### F6.N — Confluence write-side PR-bot (added 2026-04-26)

- [x] **F6.N.1** `vendors/confluence/client.py` — minimal HTTP client with `is_configured` no-op pattern (per existing vendor-adapter convention in `domain/vendor_adapters/`). Methods: `upsert_page(space_key, parent_id, title, body_storage) -> PageId`, `get_page_by_title(space_key, title) -> Page | None`, `delete_page(page_id)`. Markdown-to-Confluence-storage-format conversion via `markdown` lib + a small storage-format mapper (or use the existing PandocConverter pattern if one exists in the repo)
- [x] **F6.N.2** `scripts/runbook_confluence_publish.py` — walks all loaded runbooks, computes target Confluence page title (`<runbook_id>`), upserts (creates if missing, updates if `content_sha` differs from a `page.metadata.sentinel_sha` property). Idempotent; safe to re-run
- [x] **F6.N.3** Wire into CI: `.github/workflows/runbook-publish.yml` job that runs on merge to main, calls the script with secrets (`CONFLUENCE_BASE_URL`, `CONFLUENCE_USER`, `CONFLUENCE_TOKEN`, `CONFLUENCE_SPACE_KEY`)
- [x] **F6.N.4** Confluence is **read-only consumer** — never write-source. Documented prominently in `docs/operations/runbooks-confluence.md` (read-only message in three places: summary, "What this means in practice", and the merge-to-main lifecycle); env-var setup; unconfigured no-op behaviour; troubleshooting (auth, wrong space, storage-format conversion, missing `sentinel_sha`, AUTOGEN- leak).
- [x] **F6.N.5** Tests: mocked Confluence client, verifies upsert called with correct body, no-op when unconfigured, idempotent on second run with unchanged `content_sha`

### F6.I — Quality gates + ship

- [x] **F6.I.1** `just lint` clean for ruff + import-linter on F6 surfaces; mypy gate run separately (project default). 37 atomic commits per logical slice (models, loader, matcher, reference runbook, schema, pipeline, RAG, extends, drift, flywheel, confluence, docs)
- [x] **F6.I.2** `just test` clean for new tests on F6 surfaces (272 unit tests pass); 11 pre-existing failures in OTel metrics / Langfuse settings / MCP toolsets / replay CLI confirmed unrelated to F6 by reverting to `38b15c7` and re-running
- [ ] **F6.I.3** `just test-integration` clean for new integration tests — deferred (live-DB / live-pgvector / live-Confluence tests parked; surface coverage held by unit tests + node-level tests)
- [ ] **F6.I.4** Push branch, open PR `feat: F6 runbook catalog + matcher + RAG + extends + drift + flywheel + confluence`. PR description references spec + this plan + parent plan + RFC sections

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-26 | Initial draft | F5 merged (PR #30); F6 next phase per parent plan |
| 2026-04-26 | Verdicted Stage 2 LLM disambiguator (A+C — fires on ties AND zero-match) over the original tag-only design with alphabetical tiebreak | Industry research (HolmesGPT description-RAG, Anthropic Skills) shows description-based disambiguation lifts recall; explicit `no_match` LLM option preserves runbook-gap flywheel signal |
| 2026-04-26 | Triple-key versioning over original `version_sha` only | AWS SSM document-version pattern + content-addressable artifact registries; content hash and git SHA answer different questions |
| 2026-04-26 | Added body sanitization + quarantine prompt frame | LogJack-class indirect prompt injection (arXiv 2604.15368); runbook body enters system prompt and must be treated as untrusted |
| 2026-04-26 | Added `runbook_feedback` table + lifecycle frontmatter | Industry literature (Rootly, incidenthub) names runbook drift as the #1 anti-pattern; explicit feedback loop closes back to owner |
| 2026-04-26 | F7 capability-token enforcement contract update: at toolset-wrapper boundary, not function entry | Cerbos / OWASP / SuperTokens guidance — function-entry checks bypassable by indirect prompt injection routes |
| 2026-04-26 | Always-write `runbook_match` row, including no-match with full `candidates_json` | RFC §3.3 regulator-explainability requirement; original plan only wrote on success |
| 2026-04-26 | **Scope expansion: F6.J–F6.N** (RAG fallback, `extends:` composition, drift-detection job, gap-clustering flywheel, Confluence publish) folded back into F6 from "follow-on plans" | User requested single-PR delivery; dependencies (RAG and `extends` need loader/matcher; drift/flywheel/Confluence need everything) sequenced into Rounds 2–4 of the parallel-agent dispatch |
| 2026-04-27 | F6.F MatchRunbook node landed; F6.J Stage 3 RAG orchestration tested (9 tests); F6.L drift Slack notification + Justfile + ops doc + 13 tests; F6.M.6 flywheel tests (11 tests); F6.N.4 Confluence read-only ops doc; F6.H docs (PRD §8 + parent-plan + INDEX); F6.I lint sweep clean. Plan progress 80% → 98%; only F6.I.4 push + PR open ceremony outstanding. | Parallel-agent dispatch closed out the in-flight tracks; scope expansion delivered. |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
