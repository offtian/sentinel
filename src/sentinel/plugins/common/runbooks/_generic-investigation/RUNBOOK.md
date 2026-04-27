---
applies_to:
  alertnames: []
  exclude_labels: {}
  resource_kinds: []
  severity_min: P5
authors:
- ollie.tian
canonical_sources: []
content_sha: 76a4e764a8410386aecb7ae4b99e1e45
deprecated_at: null
description: 'Fallback exploration template used when Stage 2B returns no_match. Walks

  scope -> timeline -> saturation -> errors -> dependencies -> hypothesis.

  Read-only; flags confidence LOW; routes to compliance review.

  '
last_validated: 2026-04-26
min_match_score: 0
mnpi_safe: true
owner: sre-platform
runbook_id: _generic-investigation
superseded_by: null
tags: []
---

# Generic exploration playbook

This is the **fallback** runbook for alerts that do not match any
specific runbook in the catalog. It does not investigate any one
failure mode. Instead, it walks a structured exploration over six
dimensions and asks the agent to refine a single, falsifiable
hypothesis.

This runbook is **read-only**. Do not execute remediation. The
investigation must be flagged with `confidence=LOW` and
`requires_approval=True`, and the matcher must emit a `runbook_gap`
event so the catalog owner can author a proper runbook for this
alert class.

## Scope

Establish the perimeter of the incident before reasoning about cause.

- What is broken? Name the affected resource (pod, deployment,
  service, queue, database) and its identifier.
- Where is it broken? Cluster, namespace, region, availability
  zone, customer tenant.
- Since when? Earliest known timestamp of the symptom. Convert
  relative timings ("started 12 minutes ago") to absolute UTC.
- Who is affected? Internal teams, downstream services, end users.

## Timeline

Reconstruct the sequence of events leading up to the symptom.

- When did the symptom first appear? Pull the alert's first-fired
  timestamp.
- What changed in the half-hour before? Recent deploys, config
  changes, scaling events, feature-flag flips, secret rotations.
- Was there a preceding alert that may share a root cause? Look for
  upstream service alerts in the same window.
- Are there earlier instances of the same symptom in the recent
  history (last 24 hours, last week)?

## Saturation

Always check resource saturation before assuming a code-path bug.
A surprising fraction of incidents reduce to "we ran out of
something".

- CPU utilisation versus request and limit on the affected pods.
- Memory utilisation versus limit. Look for sudden spikes versus
  gradual climbs.
- Disk usage on data volumes; inode exhaustion can masquerade as
  application errors.
- Network throughput, packet loss, and connection-pool exhaustion.
- Queue depth and consumer lag if the path involves a message
  broker.

## Errors

Look for elevated error signal in the affected component.

- Application error rate over the last hour versus the same hour
  yesterday and same hour last week.
- Recent log lines containing `error`, `panic`, `fatal`, or stack
  traces. Sample the most recent 100 lines and the 100 lines
  preceding the first symptom timestamp.
- Distributed-trace error rate. Are spans dropping with errors at a
  particular service in the chain?
- HTTP 5xx rate at the ingress and at each hop in the call chain.

## Dependencies

Walk the upstream-dependency chain.

- What services does this component call synchronously?
- Are any of those dependencies currently alerting or showing
  elevated error rate?
- For data-store dependencies, is the database showing slow queries,
  connection-pool exhaustion, or replication lag?
- For external dependencies, check the vendor status page.

## Hypothesis

State a single, falsifiable hypothesis that one read-only check can
confirm or refute.

- The hypothesis must name a specific cause and a specific check
  that would distinguish it from alternatives.
- Surface the hypothesis as a Finding. The Finding must cite the
  evidence that motivated it (timeline entry, saturation check,
  error log line, dependency status).
- Confidence is LOW because no specific runbook applies. Mark the
  investigation `requires_approval=True` and route to the
  compliance shadow channel rather than the on-call channel.

## Remediation

Do not execute remediation from this runbook. Surface only
suggestions for an operator to consider, and only when the
hypothesis is supported by direct evidence.

## Compliance notes

This runbook fires a `runbook_gap` structured event on every
invocation. The event includes a fingerprint of the alert label set
and the classification category. Three or more matching fingerprints
within a window will draft a runbook PR for the catalog owner via
the runbook-backlog flywheel (follow-on plan).
