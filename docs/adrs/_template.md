<!--
ADR template for the Sentinel project.

File naming: `NNNN-<short-id>-<slug>.md` where `NNNN` is a zero-padded 4-digit
sequence (`0001`, `0002`, ...) that mirrors RFC `D-*` / `O-*` numbering for
traceability (so D-11 becomes ADR `0001-D11-on-prem-only.md`).

Rules:
- Every ADR must have a named decision owner before status can move to `accepted`.
- Status transitions go through PR review — propose changes via a follow-up ADR
  (`supersedes` / `superseded_by` frontmatter), not in-place edits to a
  previously-`accepted` document.
- Keep ADRs short (target ~80–150 lines). If you need more, the discussion
  belongs in an RFC; link to it.
- Use imperative mood in the Decision section ("Use on-prem vLLM"), not future
  tense ("We will use ...").
-->

---
id: "NNNN"
title: "<Short imperative title — what this ADR decides>"
status: proposed   # proposed | accepted | superseded | amended
date: YYYY-MM-DD
decision_owner: "<Named role — e.g. Head of Compliance>"
reviewers: []
rfc_refs:
  - "§N.N"
supersedes: null
superseded_by: null
---

# ADR NNNN — <Title>

## Context

What is the situation that requires a decision? Which RFC sections are in scope?
What is the working assumption going into the validation conversation? What
external constraints (compliance, infrastructure, team capacity) shape the
options?

State the decision being validated in one sentence at the top, then expand.

## Options considered

- **A. <Option name>** — short description. Tradeoffs: <one or two clauses>.
- **B. <Option name>** — short description. Tradeoffs: <one or two clauses>.
- **C. <Option name>** — short description. Tradeoffs: <one or two clauses>.

Add or remove options to match the actual decision space. Keep each bullet to
two or three lines — link to RFC sections for the long-form rationale.

## Decision

_To be filled in after the Day-N validation conversation._

State what was chosen and why, in two or three sentences. Reference the option
letter from above. Note any conditions attached to the decision (for example,
"option A, conditional on the on-prem fleet passing the BFCL tool-use eval at
≥85%").

## Consequences

_To be filled in after the Day-N validation conversation._

What changes downstream? List concrete deltas — phases of the foundations plan
that are unblocked or re-scoped, configuration values that are now fixed,
infrastructure that needs to be requested, follow-up ADRs that this triggers.

Bullets are fine. Be specific (cite phase numbers and file paths where the
change lands).

## Fallback if reversed

If the assumption captured here flips later (new policy, vendor change,
capacity issue), what is the lightest-weight remediation? Include the rough
cost estimate from RFC §11.4 so a reader can see the blast radius.

## Validation

_To be filled in after the Day-N validation conversation._

How do we know this decision is correct? For ADRs validated by stakeholder
conversation, this is typically a short list of artefacts (signed policy,
operator confirmation, working virtual key) plus the date the conversation
happened. For ADRs validated by code, link to a test or eval result.

## References

- RFC: [`Sentinel/RFC-001-sentinel-hedgefund.md`](../../Sentinel/RFC-001-sentinel-hedgefund.md)
  - Specific sections: list the sections cited in `rfc_refs` above with anchor links.
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md)
- Related ADRs: link to siblings (`./0002-D12-monorepo.md`) or successors via
  `supersedes` / `superseded_by`.
- External docs: vendor documentation, RFCs, policy documents (link, do not
  duplicate).
