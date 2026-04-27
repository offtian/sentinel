---
name: evidence-grounding
description: Forces every Finding to cite at least one evidence_ref to a recorded tool_call
version: 1.0.0
applies_to: ["*"]
---

# Evidence grounding

Every Finding produced by an investigation MUST cite at least one
`evidence_ref` that points at a recorded `tool_call`. Findings without
an evidence_ref are not Findings; they are speculation.

## The rule

A Finding is the agent's claim about what is happening or has happened.
A Finding without evidence is an opinion. Investigations that surface
opinions instead of evidence-backed findings are not actionable, are
not auditable, and erode trust in the catalog. The F8 quality gate
will drop any Finding whose `evidence_refs` list is empty or whose
`evidence_refs` reference tool calls that did not execute in this
investigation.

## Positive example

```
Finding:
  summary: Pod trading-api-7c5d9f8b6-xkpnq was OOMKilled at 08:19:47Z
    with exit code 137.
  evidence_refs:
    - tool_call:k8s_describe_pod:abc123
  confidence: HIGH
```

The Finding cites a specific tool call by its `evidence_object_id`.
The reviewer can pull the recorded tool output and verify the claim
without re-running the tool.

## Negative example (rejected)

```
Finding:
  summary: I think the pod was probably OOMKilled.
  evidence_refs: []
  confidence: MEDIUM
```

This Finding has no evidence_refs. The F8 quality gate will drop it
silently and surface a `procedural_violation` warning on the
investigation. The investigator will be asked to either (a) execute a
tool call that confirms the claim and add its evidence_object_id, or
(b) restate the Finding as a hypothesis to be tested rather than a
claim about what happened.

## Multiple evidence refs

When a Finding draws on multiple sources, list every relevant
evidence_ref. Cross-corroborated Findings (multiple independent
sources agreeing) are eligible for HIGH confidence. Findings backed
by a single source are eligible for MEDIUM at best. See
`confidence-calibration` for the full label rubric.

## What counts as a tool_call

Any recorded execution of a tool from the runbook's allowed tool set,
captured in the investigation's tool_call audit trail with a stable
`evidence_object_id`. Inferences from prior findings do not count;
each Finding must trace back to at least one direct observation.

## Why this matters

The runbook catalog is auditable because every conclusion is
traceable to a specific observation made by a specific tool at a
specific time. A regulator asking "how did you know?" should be
answerable from the audit trail alone, without re-executing the
investigation. Ungrounded findings break that contract.
