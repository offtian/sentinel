---
name: confidence-calibration
description: Calibrates HIGH / MEDIUM / LOW confidence labels to evidence quantity and quality
version: 1.0.0
applies_to: ["*"]
---

# Confidence calibration

Every Finding carries a confidence label: HIGH, MEDIUM, or LOW. The
label is what downstream consumers (the approval gate, the on-call
operator, the audit trail) read first. A miscalibrated label is
worse than no label at all: it asks the operator to either trust
evidence that does not warrant trust, or distrust evidence that
does. This skill defines how to pick the right label.

## HIGH

A Finding earns HIGH confidence when **direct causal evidence** is
**corroborated by at least one independent source**. The evidence
must show the cause itself (not just a symptom) and the
corroboration must come from a different observability dimension.

Example:
```
Finding:
  summary: Pod trading-api-xkpnq was OOMKilled at 08:19:47Z. Memory
    usage spiked from 2.1Gi to the 4Gi limit between 08:19:32Z and
    08:19:46Z, immediately after a Harness deploy of trading-api at
    08:19:10Z that landed an unbounded position-cache load.
  evidence_refs:
    - tool_call:k8s_describe_pod:abc123     # exit_code 137
    - tool_call:prom_query_range:def456     # memory spike to limit
    - tool_call:harness_recent_deploys:ghi789  # deploy at 08:19:10Z
  confidence: HIGH
```

Three independent sources (kubelet status, Prom metrics, Harness
deploy log) corroborate the cause. The cause itself (deploy landing
an unbounded cache load) is named, not just inferred.

## MEDIUM

A Finding earns MEDIUM confidence when there is **strong
correlation** but only a **single source**, OR when multiple sources
agree but they show **symptoms rather than causes**.

Example:
```
Finding:
  summary: Pod trading-api-xkpnq was OOMKilled at 08:19:47Z. Memory
    usage was at the 4Gi limit at the moment of the crash. No recent
    deploy was found.
  evidence_refs:
    - tool_call:k8s_describe_pod:abc123     # exit_code 137
    - tool_call:prom_query_range:def456     # memory at limit
  confidence: MEDIUM
```

Two sources agree on the symptom (OOM at the limit), but neither
identifies a cause beyond "memory usage was high". The hypothesis
"this is an OOM" is well supported; the hypothesis "this OOM was
caused by X" is not. MEDIUM is the right label.

## LOW

A Finding earns LOW confidence when the evidence is
**circumstantial**, when it relies on the **absence of disconfirming
evidence**, or when the matcher returned `no_match` and the
generic-investigation playbook is in use.

Example:
```
Finding:
  summary: The pod is restarting frequently and recent deploys may
    be involved, though no deploy was recorded in the last 30
    minutes and memory usage was within limits at the crash
    timestamp.
  evidence_refs:
    - tool_call:k8s_describe_pod:abc123
  confidence: LOW
```

The Finding is hedged because no specific cause is supported. LOW
findings do not get auto-published; they trigger the approval gate.
This is by design: a low-confidence claim is fine as a hypothesis
to investigate further, but is not actionable on its own.

## When in doubt, downgrade

A miscalibrated HIGH is much costlier than a miscalibrated MEDIUM.
If the evidence is borderline, choose the lower label. The approval
gate will route MEDIUM and LOW findings for human review; HIGH
findings may auto-publish per the configuration. Calibration error
in the safe direction is recoverable; in the unsafe direction it
erodes trust.
