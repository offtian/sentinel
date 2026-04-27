---
applies_to:
  alertnames:
  - KubePodCrashLooping
  - PodRestartingTooOften
  exclude_labels:
    pm_namespace:
    - pm-acme-restricted
  resource_kinds:
  - Pod
  - Deployment
  severity_min: P3
authors:
- ollie.tian
canonical_sources:
- https://github.com/prometheus-operator/runbooks/blob/main/content/runbooks/kubernetes/KubePodCrashLooping.md
content_sha: 1ab60e6a47273b8c8b1cf938b719edf8
deprecated_at: null
description: 'Procedure for investigating CrashLoopBackOff pods. Covers OOM (exit
  137),

  image pull failures (ImagePullBackOff / ErrImagePull), config and secret

  validation, dependency startup ordering, and recent-deploy correlation via

  Harness. Read-only investigation. Suggests remediation but does not execute

  it. Targeted at production K8s clusters; scoped per-namespace via

  applies_to.exclude_labels.

  '
extends: _sre-base
last_validated: 2026-04-26
min_match_score: 2
mnpi_safe: true
owner: sre-platform
runbook_id: k8s-crashloop
superseded_by: null
tags:
- key: cluster_class
  value: production
- key: investigation_type
  value: read_only
---

<!-- adapted from src/sentinel/domain/skills/k8s-crashloop-runbook/SKILL.md (F6 promotion) -->

# K8s CrashLoopBackOff investigation

## Goal

Identify the root cause of a pod that is repeatedly crashing and restarting,
correlate the crash window with recent deploys and saturation, and surface
remediation suggestions for an operator to apply. This runbook is read-only.
Do not execute remediation actions; record them as suggestions in the
investigation Findings.

## Workflow

1. **Confirm the pod is actually crash-looping.** Stale alerts are common.
   Use the cluster-scoped read tools to verify the current pod state, restart
   count, and last termination reason. Record the exit code and the
   `lastState.terminated.reason` value as the first piece of evidence.
2. **Classify the exit code.** The exit code dictates which sub-investigation
   path to take:
   - `137` (SIGKILL) generally indicates the kernel OOM-killed the container.
   - `1` is a generic application error. Almost always a config, secret, or
     dependency-connectivity failure.
   - `143` (SIGTERM) suggests graceful shutdown was interrupted, often a
     misconfigured `preStop` hook or a long-running termination handler.
   - `0` with the pod still restarting indicates the entrypoint script is
     completing instead of running as a daemon.
3. **Pull the last 100 log lines preceding the crash.** Tail logs from the
   previous container instance, not the current one. Look for stack traces,
   panics, missing-config errors, or DNS resolution failures in the final
   ten seconds before exit.
4. **Correlate with recent deploys.** Query Harness for any deploy that
   landed in the affected namespace within the last 30 minutes. A crash that
   begins immediately after a deploy almost always points at the deploy as
   the proximate cause.
5. **Check resource limits.** If the exit code is 137 or memory utilisation
   is suspected, fetch the deployment's resource requests and limits and
   compare them to recent peak utilisation from the metrics backend.
6. **Form a hypothesis and surface remediation suggestions.** Each Finding
   must cite at least one tool_call as evidence. Confidence is HIGH when the
   exit code, log evidence, and recent-deploy signal all corroborate a single
   cause. Confidence is MEDIUM when only one or two sources agree. Confidence
   is LOW when the evidence is circumstantial.

## Common root causes

### OOMKilled (exit 137)

The most common cause in trading and data-heavy workloads. Symptoms:

- Exit code 137, `lastState.terminated.reason == "OOMKilled"`.
- Memory utilisation climbing toward the configured limit immediately
  before the crash.
- Often correlates with batch-processing windows: end-of-day reconciliation,
  start-of-day position load, market-data snapshots, large Greeks
  recalculations, or report generation.

Two sub-patterns:
- **Linear growth** suggests a memory leak. Memory usage rises steadily
  over hours or days and the crash occurs once the limit is reached.
- **Sudden spike** suggests a large dataset load. Memory usage is flat
  for hours, then jumps within seconds of the crash.

### Image pull failures (ImagePullBackOff, ErrImagePull)

Symptoms in events and pod status:

- `ImagePullBackOff` or `ErrImagePull` in the pod's container statuses.
- Events containing `Failed to pull image` and an HTTP status code from
  the registry.

Common underlying causes:
- Image tag does not exist (typo in the deploy manifest, deleted tag).
- Registry credentials missing or rotated and the imagePullSecret was not
  refreshed.
- Private registry network unreachable from the node (firewall, NAT,
  egress policy change).
- Registry rate-limiting (Docker Hub anonymous pull limits).

### Config and secret validation failures (exit 1)

Symptoms:

- Exit code 1 within seconds of pod start.
- Logs contain `missing required config`, `invalid YAML`, `decryption
  failed`, or a panic mentioning a missing environment variable.

Common causes:
- ConfigMap value drift after a Helm upgrade.
- Vault dynamic-secret lease expired and the sidecar did not refresh.
- Secret rotated in Vault but not propagated to the pod (sidecar restart
  required, or the secret reference is stale).

### Dependency startup ordering

Trading and other latency-sensitive systems often have strict startup
dependency chains. Symptoms:

- Pod crashes within ten seconds of start with a connection-refused or
  DNS error in the logs.
- An init container or readiness probe targets a dependency that is not
  yet healthy.
- Restarts cluster within a deploy of an upstream service.

Common causes:
- Pricing-feed services not healthy before the OMS or risk engine starts.
- Database migrations still running when the application pod opens
  connections.
- Message broker (Kafka, RabbitMQ) not yet available when the consumer
  pod starts.

### Recent deploy correlation

A crash that begins within 30 minutes of a Harness deploy in the affected
namespace is almost always caused by that deploy. Confirm by inspecting the
deploy's diff and rollback trigger.

## Remediation suggestions (read-only --- do not execute)

These are suggestions for the operator. Do not execute them from this
runbook.

- **OOM (exit 137)**
  - Stopgap: increase the memory limit on the deployment.
  - Follow-up: profile memory usage during the crash window; look for
    leaks or large dataset loads that should be streamed.
- **Image pull failure**
  - Verify the image tag exists in the registry.
  - Refresh the `imagePullSecret` if registry credentials were rotated.
  - Confirm node-level network reachability to the registry.
- **Config or secret failure**
  - Restart the Vault agent sidecar to re-fetch the secret.
  - Diff the active ConfigMap against the chart values for the deployed
    Helm release.
- **Dependency startup ordering**
  - Confirm the upstream dependency is healthy before restarting the pod.
  - If the application has no retry logic, suggest adding init containers
    or readiness gates to enforce ordering.
- **Recent deploy correlation**
  - If the crash began within the deploy window, suggest a rollback to the
    previous revision while the root cause is investigated.

## Compliance notes

All remediation suggestions surfaced for trading-path services must be
recorded in the investigation audit trail with the operator who applied
them. This runbook is mnpi_safe (see frontmatter) but the matcher will
still skip it for investigations carrying mnpi_safe=false envelopes.
