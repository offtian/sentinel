---
applies_to:
  alertnames: []
  exclude_labels: {}
  resource_kinds: []
  severity_min: P5
authors:
- ollie.tian
canonical_sources: []
content_sha: f9d2c3a1893726c7f437e84649071574
deprecated_at: null
description: 'Shared SRE preamble (read-only contract, evidence-grounding contract,

  escalation contacts). Composed via extends into team SRE runbooks. Not

  directly selectable by Stage 1 (alertnames empty); the leading

  underscore in the runbook id signals catalog readers and operators

  that this is a base runbook, not an investigation target.

  '
extends: null
last_validated: 2026-04-26
min_match_score: 0
mnpi_safe: true
owner: sre-platform
runbook_id: _sre-base
superseded_by: null
tags: []
---

# Shared SRE base preamble

This runbook is a shared preamble inherited by every team SRE runbook
via the `extends` field. It encodes three firm-wide contracts that the
Sentinel SRE agent must honour on every investigation, regardless of
the specific failure mode being investigated.

## Read-only contract

Every SRE investigation is **read-only**. The agent's tools are scoped
to observability and discovery operations: describe pods, fetch events,
tail logs, query Prometheus and Datadog, list recent deploys. The agent
must not execute mutations. Remediation hypotheses are recorded as
suggestions on the investigation Findings and routed to a human
approver via the approval gate. A finding that proposes a mutation
without routing through the approver is a procedural-compliance
violation and the F8 quality gate must reject it.

## Evidence-grounding contract

Every Finding the agent emits **must** cite at least one
`evidence_ref` pointing at a recorded `tool_call` from the same
investigation. The matcher pre-populates the
`every_finding_has_evidence` and `evidence_within_investigation`
groundedness rules so the F8 quality gate can enforce this without
team-specific configuration. If the agent cannot produce a Finding
with evidence, it must surface a no-confidence Finding rather than
fabricating one — see the `evidence-grounding` skill for the prompt
fragment that drives this behaviour.

The matcher will not select a runbook for an investigation whose
envelope carries `pii_class == "mnpi"` unless the runbook's
`mnpi_safe` is true. The `_sre-base` runbook itself is `mnpi_safe`
because the contract it carries is policy text, not redacted data.
Every descendant must independently set its own `mnpi_safe` flag —
inheritance does not propagate that flag because each runbook owns
its own MNPI risk assessment.

## Escalation contract

Ambiguity, missing tools, unexpected errors, or any condition the
agent cannot resolve via its allowed toolset must be escalated to the
SRE platform on-call (see channel listed in your team runbook for the
exact identifier; this base runbook deliberately omits the literal
channel name to avoid a stale URL or chat-handle becoming part of the
prompt).

If the agent's overall confidence falls below the configured
`require_approval_below_confidence` threshold (default 0.7), the
investigation must hold and request human review through the approval
gate. The agent must not attempt to "rescue" a low-confidence
investigation by speculating beyond the evidence; doing so is a
groundedness violation and is rejected by the F8 quality gate.

## Compliance notes

The body of this runbook is itself part of the agent prompt at
investigation time, so it is treated as a privileged contract.
Authors of descendant runbooks must not redefine the contracts above
in narrower scope unless they explicitly intend to tighten them
(loosening is forbidden). The merge semantics in the loader make this
mostly mechanical: the child's body is appended after the parent's,
so child instructions visibly come after the contract here, never in
place of it.
