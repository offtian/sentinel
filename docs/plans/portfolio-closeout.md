# Plan: Portfolio Close-Out — Polish, Publish, Archive

**Status:** in-progress
**Created:** 2026-07-06
**Last updated:** 2026-07-07

## Goal

Turn the retired Sentinel repo into a **public, standalone portfolio artifact** on GitHub that tells
the full arc — deterministic-graph platform → blind-spot review → production rebuilt on a minimal
Claude Agent SDK loop — and then archive it read-only. A separate greenfield successor repo
(cross-linked, out of scope here) will carry the working code that embodies the lesson.

Driven by the impact-ranked interview of 2026-07-06 (decision log below). This is **close-out
meta-work**, not a resumption of any retired feature plan.

### Decision log (interview 2026-07-06)

| # | Question | Answer | Impact |
|---|----------|--------|--------|
| 1 | Any instance still receiving real webhooks? | Fully retired, never served real traffic | §1 security findings archival; no emergency work |
| 2 | What is this repo now? | Active portfolio piece | Overrides review §8.1 "don't green `main`" — polish is in scope |
| 3 | Audience? | Both skimmers and code-readers | README narrative *and* code credibility both funded |
| 4 | Public or private? | Public, standalone on profile | Pre-publish scrub, license change, archive read-only at the end |
| 5 | Is the production successor showable? | No — private work code | Public story must be self-contained |
| 6 | Where does the public successor live? | Separate greenfield repo, cross-linked | This repo stays "polish → publish → archive"; case study can be leaner |
| 7 | Deadline? | No rush | Do it properly; option to publish both repos together |

### Acceptance criteria

- [ ] `just lint` and `just test` fully green on `main`
- [ ] No top-level doc makes a claim the code contradicts (review §1.5, §3.6 items fixed)
- [x] Case study exists and is linked from the top of the README
- [x] MIT LICENSE in place; "Private project" wording gone
- [ ] Full git history passes a secrets scan; no unintended personal/internal references
- [ ] Repo public, cross-linked with the successor, then archived read-only on GitHub

## Scope

### In scope

- One-line stale-test fix to green `main` (`tests/unit/test_bootstrap_otel.py:109`)
- Doc-truth fixes: §1.5 (docs claim LangGraph is the running pipeline; flag-off default actually
  runs the archived Pydantic Graph path) and §3.6 (dead `graphify-out/` pointer in CLAUDE.md,
  stale `architecture.md` reference paths, README diagram omitting `assess_quality`)
- Case study document telling the map-vs-territory arc
- README rework for portfolio framing + forward link to successor
- MIT license
- Pre-publish scrub (secrets scan over full history, personal/internal-reference check)
- Making the repo public and archiving it read-only

### Out of scope

- **All feature/fix work from the blind-spot review** beyond doc accuracy: no auth, no A/B repair,
  no HolmesGPT re-enable, no eval fixes, no confidence-machinery repair (review §8.1 stands)
- Resuming any abandoned plan (LangGraph T21–T30, metrics wiring, etc.)
- **The successor build itself** — separate greenfield repo; only its charter is sketched here (§Successor charter)

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| License | MIT | Portfolio default; maximally readable signal, no friction for reviewers |
| §1.5 fix direction | Fix the **docs** to describe reality (flag-off default = archived pipeline; LangGraph path flag-gated, migration stopped at retirement) | Zero behaviour change in a retired repo; flipping the flag default to match the docs would dress up a migration that never finished |
| Case study location | `docs/case-study.md`, linked from the top of README | Skimmers get the story in one click; keeps README itself tight |
| Case study depth | Lean — arc + principles, pointing to the blind-spot review for evidence | Successor repo will carry working code; review §8.2 already holds the detailed lessons |
| CLAUDE.md graphify section | Delete | `graphify-out/` does not exist; it's an instruction to trust a phantom file |
| Scrub tool | `gitleaks` over full history + manual grep for emails/internal names | Standard, fast, catches the common cases |
| Dirty-history contingency | Decide at Stage 3 gate: history rewrite vs publish with fresh squashed history | Don't pre-commit to a disruptive rewrite before knowing if it's needed |
| Archive timing | Archive only after successor repo is public and cross-links are live | "No rush" answer allows both repos to land together so the arc is complete on day one |

## Steps

### Stage 1 — Truth pass (code + docs agree, `main` green)

- [x] Fix stale assertion in `tests/unit/test_bootstrap_otel.py:109` (`otel.traces.disabled` → `otel.traces.no_backend`); run `just test` to confirm fully green
      — also required pinning `langfuse_host=None` on the settings stub (truthy MagicMock attr skipped the branch)
- [x] Fix §1.5 in README / CLAUDE.md / `docs/architecture.md`: state that the default execution
      path is the archived Pydantic Graph pipeline and the LangGraph path is flag-gated
      (`LANGGRAPH_SRE_ENABLED=false` default); remove/soften "reference-only, forbidden" claims
      that contradict `worker.py` — AGENTS.md and `docs/prd.md` line 82 carried the same claim and were fixed too
- [x] Fix §3.6 pointers: delete CLAUDE.md graphify section; correct `architecture.md` key-reference
      paths (`interfaces/graphs/investigation.py` → `_archive/`, ReplayBundle → `utils/replay_bundle.py`);
      note `assess_quality` in the README pipeline diagram text
- [x] Gate: `just lint` + `just test` green (1430 passed / 41 skipped; 5 import contracts kept); grep
      found no remaining contradicted claims; README banner's "expect `main` to be red" updated to match

### Stage 2 — Narrative

- [x] Write `docs/case-study.md`: the bet (deterministic graph + runbook contracts) → what was
      built (F1–F8) → the blind-spot review → the production learning (minimal Claude Agent SDK
      loop, read-only guardrails, better tool use) → principles carried forward (review §8.2)
- [x] Rework README top: portfolio framing, arc headline, links to case study + blind-spot review,
      placeholder link to successor repo; case study + review added to the Documentation table
- [x] Add MIT `LICENSE` file; replace README "Private project. All rights reserved." section
      (grep confirms the wording is gone repo-wide)

### Stage 3 — Pre-publish gate

- [ ] Run `gitleaks` (or equivalent) over the full git history; triage findings
- [ ] Grep history and tree for personal emails, employer/internal names, and anything not meant
      for public view (`.env.default`, docker-compose, docs)
- [ ] Confirm the seeded Langfuse/MinIO dev credentials are documented as dev-only (they are —
      verify wording survives the README rework)
- [ ] **Decision gate:** if history is dirty → choose rewrite vs fresh squashed history before proceeding

### Stage 4 — Publish + archive

- [ ] Merge close-out branches to `main`; confirm CI green on `main`
- [ ] Make repo public
- [ ] When the successor repo is public: add cross-links both ways (README of each)
- [ ] Archive the repo read-only on GitHub (final act — nothing lands after this)

## Successor charter (separate repo — not executed here)

Greenfield public repo demonstrating the harvested lessons (review §8.2): a **minimal Claude Agent
SDK investigation loop** — bash + PromQL + Loki tools behind an always-on, fail-closed, read-only
guardrail — wrapped by the *separable* compliance layer: append-only audit log, human approval gate
that actually notifies a human, per-call trace/token capture, authenticated ingress with accountable
approver identity, idempotent outbound posts, time-based stale-work reaping, and a thin unmocked
eval on real incident fixtures. No deterministic replay, no capability tokens, no orchestration graph.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-07-07 | Stage 2 complete on `docs/closeout-stage2-narrative` (stacked on stage 1). Case study kept lean per design decision; README doc table reordered to lead with narrative. Noted for Stage 3: git history contains a `kraken.tech` work email among author identities — triage at the scrub gate. | Narrative per plan |
| 2026-07-07 | Stage 1 complete on `chore/closeout-stage1-truth-pass`. Test fix needed one extra line (stub `langfuse_host=None`); §1.5 fixes extended to AGENTS.md + prd.md which repeated the claim; README banner updated since `main` will be green post-merge. Local-only note: `lint-imports` was failing because macOS `UF_HIDDEN` flags on `.venv` `.pth` files make Python 3.13's `site` skip them — cleared with `chflags nohidden`, no repo change. | Truth pass per plan |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
