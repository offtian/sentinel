# F0 Validation-Sprint Conversation Prompts

The F0 validation sprint (week 0.5 of the foundations plan) confirms or flips six tentative decisions in
[RFC-001](../../Sentinel/RFC-001-sentinel-hedgefund.md) — D-11..D-16 plus the re-opened agent-framework
question O-10. Each conversation produces one ADR (`0001`–`0006`); flips become RFC amendments per
[RFC §11.4](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions).
Work does **not** block on the sprint — F1+ proceeds against the working assumption, and any flip is a
documented delta, not a restart. This file is the meeting playbook the engineer running each conversation
brings into the room.

| Day  | Decision | Stakeholder                                   | ADR file                                                               |
|------|----------|-----------------------------------------------|------------------------------------------------------------------------|
| 1    | D-12     | Tech Lead, Platform Engineering               | [0002-D12-monorepo](0002-D12-monorepo.md)                              |
| 2    | D-11     | Head of Compliance / Risk LLM-policy owner    | [0001-D11-on-prem-only](0001-D11-on-prem-only.md)                      |
| 2–3  | O-10     | Senior engineer advocating PydanticAI+LangGraph | [0006-O10-pydanticai-langgraph](0006-O10-pydanticai-langgraph.md)    |
| 3    | D-13     | LiteLLM proxy operator                        | [0003-D13-firm-shared-infra](0003-D13-firm-shared-infra.md)            |
| 4    | D-15     | Langfuse operator                             | [0004-D15-langfuse-rbac](0004-D15-langfuse-rbac.md)                    |
| 4    | D-16     | Database team / DBA                           | [0005-D16-postgres-pgvector](0005-D16-postgres-pgvector.md)            |

---

## Day 1 — Monorepo onboarding (D-12)

**Stakeholder:** Tech Lead, Platform Engineering • **ADR:** [0002-D12-monorepo](0002-D12-monorepo.md)

### Purpose
Confirm whether Sentinel lives as a sub-package inside the firm's platform monorepo or as a greenfield
standalone repo. Lock in CI/CD, lint, and review conventions so F1 starts against a known scaffold.

### Working assumption
> "Codebase as a sub-package inside the firm's platform monorepo." —
> [RFC §11.1, D-12](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)

### Materials to bring
- [RFC §6 deployment topology](../../Sentinel/RFC-001-sentinel-hedgefund.md#6-deployment-topology-rbac-network)
- RFC §14 plan (week-by-week build) — link in same RFC
- This plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md) (F1–F8 phasing)
- The greenfield repo's current `pyproject.toml` (lint contracts, Python 3.13 pin)

### Agenda
1. State goal: pick repo style + CI/CD scaffold so F1 begins on Day 6.
2. Walk through RFC §6 — agent runs in DevOps cluster, multi-cluster RBAC story.
3. Ask the Key Questions (below).
4. Inspect one example service that lives in the monorepo today; note its CI config.
5. Capture branching strategy + code-review SLA.
6. Agree fallback trigger ("if monorepo onboarding takes >5d, fall back to standalone").
7. Sign-off: confirm tech-lead-of-record name and SLA for first PR review.

### Key questions
- One-service-per-repo or sub-package style for cross-cutting platform services?
- What's the CI/CD config (Bazel? Buildkite? something custom)? Can a Python 3.13 sub-package opt into
  its own ruff/mypy contract or must it conform to the firm-wide ruleset?
- Where's the canonical example service we can mirror — link to repo path?
- Branching strategy: trunk-based with short-lived branches, or release branches per platform team?
- Code-review SLA + required reviewer count — does Sentinel need a platform-wide review or a team review?
- Are import-linter contracts honoured by CI, or only ruff/mypy?

### Decision criteria

| If they say…                                                    | Decision                                  | Action                                              |
|-----------------------------------------------------------------|-------------------------------------------|-----------------------------------------------------|
| Sub-package fits, here's the conventions doc + example service  | Confirm D-12: monorepo sub-package        | F1 unchanged. Capture CI/lint deltas in ADR Consequences. |
| Standalone is normal for new platforms (risk/new-code carve-out)| Flip D-12: greenfield standalone repo     | Half-day CI scaffold delta in F1.0 (new pre-step).  |
| Onboarding takes >5 working days                                | Defer: start standalone, migrate later    | Document migration trigger criteria in ADR Fallback. |

### What "yes" looks like
- Tech lead names a slot in the monorepo and points at an example service to mirror.
- CI/CD scaffold lands in F1 with no extra time budget.
- Code-review SLA documented; at least one named reviewer assigned for first 5 PRs.

### What "no/maybe" requires
- Flip ADR 0002 to "standalone repo"; add F1.0 sub-step to scaffold CI from scratch (~half day).
- Loop in: SRE on-call lead (so monorepo dashboards still see Sentinel logs) and security
  (separate-repo secret-store onboarding may differ).
- Update F1 phase header to mention "if standalone, also wire CI artefact upload to firm registry".

### Capture template
```
Decision (YYYY-MM-DD): <option chosen>
Decided by: <name>, <role>
Reasoning: <one paragraph>
Consequences for plan phases: <which F1..F8 sub-steps shift>
Follow-ups: <any open items>
```

---

## Day 2 — Compliance LLM policy (D-11)

**Stakeholder:** Head of Compliance (or whoever signed the firm's LLM policy) •
**ADR:** [0001-D11-on-prem-only](0001-D11-on-prem-only.md)

### Purpose
Confirm the on-prem-only constraint, the approved-on-prem-model list, and whether any "yes-with-conditions"
exception (e.g. external+VPC for the investigator) is open to us. Sets the model allowlist for F4/F5.

### Working assumption
> "All LLM calls via LiteLLM proxy → on-prem vLLM endpoints only. No external providers." —
> [RFC §11.1, D-11](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)

### Materials to bring
- The signed firm LLM-use policy (request a copy in advance)
- [RFC §8 risk register](../../Sentinel/RFC-001-sentinel-hedgefund.md#8-risks-compliance-regulatory-replay)
- [RFC §3 PII classes](../../Sentinel/RFC-001-sentinel-hedgefund.md#3-data-modelpipeline-io-at-every-stage)
  (especially the `pii_class` enum: `public/internal/confidential/mnpi`)
- [RFC §2.4 LiteLLM chokepoint](../../Sentinel/RFC-001-sentinel-hedgefund.md#24-litellm-proxy-as-the-llm-chokepoint--on-prem-only-d-11)
- The redactor design (RFC §3.6) so we can ask "is this redactor sufficient for `internal`-class data?"

### Agenda
1. State goal: confirm allowlist + redaction posture, not "ask permission for cloud LLMs".
2. Walk through RFC §8.1 risk register — frame each risk against current redactor design.
3. Ask the Key Questions (below); explicitly ask "yes-with-conditions" rather than yes/no.
4. Capture approved on-prem model list + any blacklist (e.g. specific Llama variants ruled out).
5. Agree fallback: what would a "compliance-blessed investigator-only external+VPC exception" require?
6. Lock owner sign-off: name in ADR Reviewers; agree quarterly re-review cadence.
7. Set follow-up: 5-business-day SLA on confirming the model list.

### Key questions
- Does the signed policy permit any external+VPC use (e.g. Anthropic via Bedrock VPC endpoint) for
  `internal`-class data, or is it strictly on-prem-only across all `pii_class` values?
- What is the current approved on-prem model list and who maintains it?
- Is the redactor design (LLM-judge + regex layer, runs before any external boundary) sufficient for
  `confidential` and `mnpi` classes, or does it need additional review?
- How is the `pii_class` boundary enforced — at LiteLLM proxy, at app layer, or both?
- What would a compliance-blessed "investigator-only external+VPC exception" require — separate signed
  policy, separate audit pathway, or just a flag in the model allowlist?
- Retention policy on raw tool outputs (RFC §11.2 O-06) — 90d default, or 1y for hedge fund?

### Decision criteria

| If they say…                                                    | Decision                                                  | Action                                                   |
|-----------------------------------------------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| Strictly on-prem; here's the model allowlist                    | Confirm D-11: on-prem-only                                | F1–F8 unchanged. Capture allowlist in ADR Consequences.  |
| Yes-with-conditions: external+VPC for investigator on `internal` only | Amend D-11: tiered allowlist by `pii_class`         | Note ~3d delta in month 3 (RFC §11.4). Foundations stay on-prem. |
| Need follow-up review by Risk before answering                  | Hold: F4/F5 proceed against on-prem assumption            | Set named SLA (5 business days). Document escalation path. |

### What "yes" looks like
- Signed-off model allowlist captured in ADR (e.g. Llama 3.3 70B, Qwen 2.5 72B, DeepSeek-V3).
- Redactor design accepted as sufficient for `internal`-class; `confidential`/`mnpi` route on-prem-only.
- Quarterly re-review cadence agreed; named compliance owner in ADR Reviewers.

### What "no/maybe" requires
- If external+VPC OK for investigator: ADR 0001 amended; F4 LiteLLM virtual-key config gains an
  `external_vpc_allowed` flag per `pii_class`. Loop in LiteLLM operator (D-13) immediately so the virtual
  key already supports the routing rule.
- If redactor judged insufficient: F8 quality-gate scope expands; loop in security-engineer to harden
  PII redaction layer before F4 ships.

### Capture template
```
Decision (YYYY-MM-DD): <option chosen>
Decided by: <name>, <role>
Reasoning: <one paragraph>
Consequences for plan phases: <which F1..F8 sub-steps shift>
Follow-ups: <any open items>
```

---

## Day 2–3 — Agent framework re-eval (O-10)

**Stakeholder:** Senior engineer advocating PydanticAI + LangGraph •
**ADR:** [0006-O10-pydanticai-langgraph](0006-O10-pydanticai-langgraph.md)

### Purpose
Settle the **agent framework** decision: PydanticAI vs OpenAI Agents SDK. The orchestration framework
(LangGraph vs Pydantic Graph) is a **separate** decision, tracked in ADR 0007 during F5. Be explicit
about that split in the room — the conversation should not conflate the two.

### Working assumption
> RFC v0.4 default: confirm **PydanticAI** for the LLM-loop layer; orchestration choice deferred to F5. —
> [RFC §15.14](../../Sentinel/RFC-001-sentinel-hedgefund.md#1514-agent-framework-re-evaluation-openai-agents-sdk-vs-pydanticai--langgraph)

### Materials to bring
- The senior engineer's POC code (request in advance)
- [RFC §15.14 comparison matrix](../../Sentinel/RFC-001-sentinel-hedgefund.md#1514-agent-framework-re-evaluation-openai-agents-sdk-vs-pydanticai--langgraph)
- [RFC §2.3 Agent framework: PydanticAI + LangGraph](../../Sentinel/RFC-001-sentinel-hedgefund.md#23-agent-framework-pydanticai--langgraph)
- The current Sentinel codebase (already on PydanticAI) — show as reference for "what shipping looks like"
- PR #15 (`prompt-versioning-and-replay`) for the existing replay-bundle pattern
- BFCL + custom Sentinel-tool-fixture eval harness if available

### Agenda
1. State goal: pick agent framework only — orchestration is F5/ADR 0007.
2. Senior engineer walks through their POC: tool-loop shape, replay path, OTEL span emission.
3. Ask the Key Questions (below); demo replay determinism live if possible.
4. Compare against current Sentinel (PydanticAI) — what would change in F1–F4 if we flipped to OpenAI Agents SDK?
5. Tool-use eval results: BFCL scores against Llama 3.3 70B and Qwen 2.5 72B through both candidates.
6. Lock decision; if no consensus, escalate to CTO / Head of Platform Eng for tie-break (per RFC §11.4).
7. Set follow-up: orchestration framework decision deferred to F5, owner = same senior engineer.

### Key questions
- Walk me through your POC's tool-call loop — show me a recorded trace and a replay, end-to-end.
- What are the tool-use eval scores against on-prem Llama 3.3 70B and Qwen 2.5 72B? On what eval set?
- How does PydanticAI's `instrument=True` OTEL emission compare to OpenAI Agents SDK's custom `TraceProcessor`?
  Span attribute coverage? Cost field?
- Replay determinism: PydanticAI + ReplayBundle (PR #15 shape) vs LangGraph checkpoint replay — which
  do you prefer and why? (Note: orchestration choice is ADR 0007.)
- Team familiarity at the firm: how many other teams ship on PydanticAI vs OpenAI Agents SDK today?
- Auditability: which framework has a longer track record in compliance-sensitive deployments?

### Decision criteria

| If they say…                                                    | Decision                                                  | Action                                                   |
|-----------------------------------------------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| POC clears tool-use bar (≥85% on Llama 3.3 70B); team prefers PydanticAI | Confirm O-10: PydanticAI                          | F1–F8 unchanged. Capture eval scores in ADR.             |
| POC fails tool-use bar; OpenAI Agents SDK wins eval             | Flip O-10: OpenAI Agents SDK                              | F4 agent-loop rewrite (~5d). F5 still picks orchestration separately. |
| Inconclusive — both pass eval, no clear team preference         | Default: PydanticAI (RFC v0.4 default + Sentinel codebase already there) | Schedule re-eval at end of F4 with real Sentinel tools.  |

### What "yes" looks like
- Tool-use eval scores attached to ADR; clear winner above 85% threshold.
- Replay path demoed end-to-end; deterministic across re-runs.
- Senior engineer signs off; named in ADR Reviewers.

### What "no/maybe" requires
- If flipping to OpenAI Agents SDK: F4 sub-steps for agent-loop rewrite (~5d delta). Loop in
  ai-engineer + platform-engineer teammates. Update F4 acceptance criteria.
- If inconclusive: stay PydanticAI but explicitly mark ADR Status as "Provisional — re-eval at F4 close".
  Note that this does NOT block F1–F3 work.

### Capture template
```
Decision (YYYY-MM-DD): <option chosen>
Decided by: <name>, <role>
Reasoning: <one paragraph>
Consequences for plan phases: <which F1..F8 sub-steps shift>
Follow-ups: <any open items>
```

---

## Day 3 — LiteLLM proxy operator (D-13 partial)

**Stakeholder:** LiteLLM proxy operator (firm platform team) •
**ADR:** [0003-D13-firm-shared-infra](0003-D13-firm-shared-infra.md)

### Purpose
Get a virtual key tagged for Sentinel's tenant-routing model, confirm the OTLP routing target is the
firm Langfuse, and capture the tool-use-validated on-prem model list. This unblocks F4 (Langfuse OTLP
+ replay bundle) and F5 (LiteLLM virtual-key wiring).

### Working assumption
> "Reuse firm-existing infra: LiteLLM proxy, OTEL collector, Langfuse, shared Postgres cluster." —
> [RFC §11.1, D-13](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)

### Materials to bring
- [RFC §2.4 LiteLLM chokepoint](../../Sentinel/RFC-001-sentinel-hedgefund.md#24-litellm-proxy-as-the-llm-chokepoint--on-prem-only-d-11)
- [RFC §13.6 OTEL collector config](../../Sentinel/RFC-001-sentinel-hedgefund.md#136-otel-collector-configuration-the-redaction-layer)
- The compliance-approved model allowlist (output of D-11 conversation, likely held verbally if D-11 not yet closed)
- Tag spec: `tenant_id`, `team_profile` (`sre|devops|ace`), `pii_class` (`public|internal|confidential|mnpi`)
- Sentinel's settings.py boundary so we know what env-var names to wire (LITELLM_BASE_URL, LITELLM_VIRTUAL_KEY)

### Agenda
1. State goal: virtual key + OTLP destination + model list.
2. Walk through RFC §2.4 — concrete shape, what we operate vs what they operate.
3. Ask the Key Questions (below).
4. Issue a test virtual key and run a smoke call from a dev machine.
5. Confirm OTLP routing destination = firm Langfuse (cross-references D-15).
6. Capture rate limits, failover behaviour, audit log location.
7. Set follow-up: connection details captured in 1Password / CI secret store, not the repo.

### Key questions
- Can you issue a virtual key tagged with `tenant_id` / `team_profile` / `pii_class`? How are tags
  propagated to OTEL spans?
- Which on-prem models pass your tool-use eval today (BFCL or equivalent), and what scores?
- Where is the OTLP collector? Does it route directly to Langfuse, or via the firm's central OTEL
  collector with redaction at the gateway (RFC §13.6)?
- What rate limits + budget enforcement apply per virtual key? Per `tenant_id`?
- Failover behaviour: if the primary on-prem cluster degrades, does LiteLLM auto-fail over to a
  secondary cluster, and which model does the fallback serve?
- Where do LiteLLM audit logs live, and for how long? Is the trace replayable from those alone?

### Decision criteria

| If they say…                                                    | Decision                                                  | Action                                                   |
|-----------------------------------------------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| Virtual key issued with all 3 tags + OTLP confirmed             | Confirm D-13 partial: LiteLLM reuse                       | F4/F5 unchanged. Capture key tags + OTLP target in ADR.  |
| Tags partially supported (e.g. `tenant_id` only)                | Amend D-13: enrich tags app-side, log delta               | Loop in ai-engineer to inject `team_profile`/`pii_class` as request headers. |
| LiteLLM proxy unavailable / not yet productionised              | Flip D-13: stand up own LiteLLM (~3d cost per RFC §11.4)  | Add F1.5 sub-step "deploy LiteLLM Helm chart"; loop in platform-engineer. |

### What "yes" looks like
- Virtual key issued; smoke call returns 200 from a tool-using prompt against an on-prem 70B model.
- OTLP routing target confirmed as the same Langfuse instance D-15 covers.
- Tool-use-validated model list captured in ADR; matches D-11 allowlist.

### What "no/maybe" requires
- If we have to stand up our own LiteLLM: ADR 0003 flips, ~3d delta in F1, loop in platform-engineer.
- If only `tenant_id` propagates: app-side enrichment logic in F2 (envelope-to-header mapping). Loop in
  ai-engineer to add the header layer.
- If audit logs are absent or short retention: F8 replay-bundle scope expands to capture LiteLLM
  request/response inline.

### Capture template
```
Decision (YYYY-MM-DD): <option chosen>
Decided by: <name>, <role>
Reasoning: <one paragraph>
Consequences for plan phases: <which F1..F8 sub-steps shift>
Follow-ups: <any open items>
```

---

## Day 4 — Langfuse operator (D-15)

**Stakeholder:** Langfuse operator (firm platform team) •
**ADR:** [0004-D15-langfuse-rbac](0004-D15-langfuse-rbac.md)

### Purpose
Confirm Langfuse version, project-level RBAC, and tag-based filtering on `tenant_id` are sufficient to
run one project per platform team (`sentinel-sre`, `sentinel-devops`, `sentinel-ace`, `sentinel-platform`).
Test creating a `sentinel-sre` project end-to-end during the meeting if possible.

### Working assumption
> "Per-team Langfuse projects accessed via the firm's existing Langfuse instance, with project-level
> RBAC + trace-tag filtering on `tenant_id`." —
> [RFC §11.1, D-15](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)

### Materials to bring
- [RFC §7.2 Langfuse projects per platform team](../../Sentinel/RFC-001-sentinel-hedgefund.md#72-langfuse-projects-one-per-platform-team-not-per-pm)
- [RFC §13.6 OTEL → Langfuse OTLP routing](../../Sentinel/RFC-001-sentinel-hedgefund.md#136-otel-collector-configuration-the-redaction-layer)
- The proposed project structure (4 projects under `hedgefund-platform-eng` org, per RFC §7.2)
- Sample trace payload with `tenant_id` tag so we can test filtering live

### Agenda
1. State goal: confirm 4-project structure works + tag filtering RBAC viable.
2. Walk through RFC §7.2 — operator boundary > tenant boundary, redactor runs before export.
3. Ask the Key Questions (below).
4. Live: create `sentinel-sre` project, push a test trace, attempt tag-filtered RBAC view.
5. Confirm OTLP endpoint, retention, SSO integration, hosted vs self-hosted (RFC §7.2 strongly recommends self-host).
6. Capture project-creation procedure for D-15 follow-ups.
7. Set follow-up: `sentinel-platform` project creation gates F4 acceptance.

### Key questions
- Which Langfuse version is deployed (v3+ supports project-level RBAC)? Is it self-hosted or cloud?
- Does project-level RBAC support per-user tag-based filtering on `tenant_id`, strongly enough that a
  user scoped to `tenant=fund-a` cannot see `tenant=fund-b` traces in the same project?
- What's the OTLP endpoint? Does it require an API key or service-account auth at the OTLP layer?
- Retention: how long are traces kept? Is there a per-project override?
- SSO: does it integrate with the firm IdP, and are role mappings handled in Langfuse or upstream?
- Project-creation procedure: who can create new projects, and what's the SLA?
- If self-hosted, where is the instance and what's the upgrade cadence?

### Decision criteria

| If they say…                                                    | Decision                                                  | Action                                                   |
|-----------------------------------------------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| v3+ self-hosted, project RBAC + tag filtering work as needed    | Confirm D-15: 4-project structure                         | F4 unchanged. Capture project IDs + OTLP endpoint in ADR. |
| RBAC weak — tag filter is best-effort or not enforced server-side | Amend D-15: one Langfuse instance per team profile (3 instances) | F4 grows: deploy 3 self-hosted instances; loop in platform-engineer (~3d delta). |
| v2 or earlier without RBAC                                      | Flip D-15: self-host new Langfuse for Sentinel only       | F1.6 new sub-step: deploy Langfuse Helm chart on-prem.   |

### What "yes" looks like
- `sentinel-sre` project created live; test trace appears with `tenant_id` tag; tag-filtered RBAC blocks
  cross-tenant view as expected.
- OTLP endpoint + auth captured; matches D-13 LiteLLM OTLP target.
- SSO works; named operator in ADR Reviewers.

### What "no/maybe" requires
- If RBAC weak: flip to one-instance-per-team-profile; add 3-instance deploy step to F1; update F2/F4
  envelope code so `team_profile` selects the correct OTLP endpoint at runtime.
- If Langfuse not deployed yet: flip to self-host for Sentinel only (F1 grows ~3d). Loop in
  platform-engineer.
- If self-host violates a firm policy: escalate to Compliance (D-11 owner) before resolving — could
  trigger an amendment to ADR 0001 too.

### Capture template
```
Decision (YYYY-MM-DD): <option chosen>
Decided by: <name>, <role>
Reasoning: <one paragraph>
Consequences for plan phases: <which F1..F8 sub-steps shift>
Follow-ups: <any open items>
```

---

## Day 4 — Postgres + pgvector (D-16)

**Stakeholder:** Database team / DBA • **ADR:** [0005-D16-postgres-pgvector](0005-D16-postgres-pgvector.md)

### Purpose
Confirm `pgvector` extension is available on the shared cluster, that we can request per-database role
separation (`sentinel_app` vs `sentinel_audit`), and that backup/restore SLAs and audit features fit our
WORM requirements. Note: pgvector is for case-history retrieval, which is **out of foundations scope**
— so a "no" on pgvector becomes a month-3 problem, not an F-phase blocker. Capture that nuance in the ADR.

### Working assumption
> "Use firm's shared Postgres cluster — request `sentinel_app` and `sentinel_audit` databases (split
> for the WORM role separation in §12.3.10)." —
> [RFC §11.1, D-16](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)

### Materials to bring
- [RFC §12.3 schema](../../Sentinel/RFC-001-sentinel-hedgefund.md#123-what-we-do-store-in-sentinel-app-db-and-why)
  (especially `audit_log` WORM, `case_history` with `pgvector`)
- [RFC §3.3.1 case-history retrieval](../../Sentinel/RFC-001-sentinel-hedgefund.md#331-stage-25--case-history-retrieval-similar-past-investigations)
  (so DBA understands what `pgvector` is for)
- Foundations plan F3 (DB schema gap-fill) so DBA sees the migration plan
- Sample WORM trigger SQL from RFC §12.3.10 (the trigger blocking UPDATE/DELETE on audit_log)

### Agenda
1. State goal: confirm extensions, role separation, backup SLAs.
2. Walk through RFC §12.3 — 8 canonical tables, audit log WORM, RLS policies.
3. Ask the Key Questions (below).
4. Request `sentinel_app` and `sentinel_audit` databases on shared cluster.
5. Confirm `pgvector` availability — if no, agree fallback (case-history on dedicated small RDS later).
6. Capture backup/restore SLA, pgaudit availability, logical replication for WORM archive.
7. Set follow-up: connection strings captured in 1Password.

### Key questions
- Is `pgvector` extension available on the shared cluster? Which version, and what embedding dimensions
  does it support (we'll need 768 or 1024 for sentence-transformer-class embeddings)?
- Can we request per-database role separation: `sentinel_app` (RW) vs `sentinel_audit` (append-only,
  separate role with no UPDATE/DELETE grants)?
- Is `pgaudit` available, and is it enabled by default or opt-in? Where do its logs go?
- Backup/restore SLA: PITR window, RPO, RTO? Test-restore cadence?
- Logical replication: can we set up a slot replicating `audit_log` to a long-term WORM archive (S3
  Glacier or equivalent)?
- Row-level security (RLS) — is it enabled on shared cluster, and does the cluster monitor for queries
  that bypass RLS via SECURITY DEFINER?

### Decision criteria

| If they say…                                                    | Decision                                                  | Action                                                   |
|-----------------------------------------------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| pgvector available + role separation + pgaudit + logical replication all green | Confirm D-16: shared cluster                  | F3 unchanged. Capture connection strings + extension list in ADR. |
| Most green, but no pgvector                                     | Amend D-16: shared cluster for app+audit; pgvector deferred to month 3 | F3 unchanged. Add ADR Consequences note: "case-history needs dedicated RDS in month 3 (~2d cost)." |
| No role separation OR no WORM-compatible feature set            | Flip D-16: dedicated small RDS for Sentinel               | F1 grows ~3d (provisioning); F3 unchanged after that.    |

### What "yes" looks like
- `sentinel_app` and `sentinel_audit` databases provisioned on shared cluster.
- `pgvector` confirmed (or scoped as month-3 problem with named owner).
- pgaudit enabled, backup SLA documented (PITR window meets compliance R-CO-* needs).
- Logical replication slot for `audit_log` set up or scheduled.

### What "no/maybe" requires
- If no pgvector: ADR 0005 records "pgvector deferred"; F3 stays unchanged; add a TODO to the case-history
  plan (month 3) flagging the dedicated-RDS provisioning.
- If no role separation: flip to dedicated RDS; add F1 sub-step for provisioning; loop in
  platform-engineer + security-engineer.
- If no logical replication for WORM archive: F3.6 audit-log WORM trigger remains the
  enforcement, but document the gap in the ADR — full external WORM archive becomes a v1 followup
  with security-engineer.

### Capture template
```
Decision (YYYY-MM-DD): <option chosen>
Decided by: <name>, <role>
Reasoning: <one paragraph>
Consequences for plan phases: <which F1..F8 sub-steps shift>
Follow-ups: <any open items>
```
