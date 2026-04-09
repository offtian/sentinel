---
name: k8s-crashloop-runbook
description: Procedure for investigating CrashLoopBackOff pods and identifying restart root causes
version: 1.0.0
applies_to: ["k8s_*", "kubernetes_crashloop"]
---

# K8s CrashLoopBackOff Runbook

## 1. Identify the Affected Pod and Namespace

Run the following to get crash context:

```bash
kubectl get pods -n <namespace> --field-selector=status.phase!=Running
kubectl describe pod <pod-name> -n <namespace>
kubectl get events -n <namespace> --sort-by='.lastTimestamp' | grep <pod-name>
```

Record the restart count, last termination reason, and exit code from `describe` output.

## 2. Classify the Exit Code

| Exit Code | Meaning | Common Cause |
|-----------|---------|-------------|
| 137 | OOMKilled (SIGKILL) | Memory limit exceeded -- check position dataset size or risk calc batch |
| 1 | Application error | Config missing, failed dependency connection, bad secret |
| 143 | SIGTERM | Graceful shutdown interrupted -- check preStop hooks |
| 0 + restart | Success exit in loop | Entrypoint script completing instead of running as daemon |

## 3. OOM Investigation (Exit Code 137)

This is the most common cause in trading infrastructure. Large position datasets, end-of-day P&L snapshots, and Greeks recalculations can spike memory.

1. Check current memory usage vs limits:
   ```bash
   kubectl top pod <pod-name> -n <namespace>
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[*].resources}'
   ```
2. Query Datadog for memory trend leading up to the crash:
   ```
   kubernetes.memory.usage{pod_name:<pod-name>} by {container_name}
   ```
3. Check if the crash correlates with batch processing windows (EOD reconciliation, SOD position load, market data snapshot).
4. If memory usage is climbing linearly, suspect a leak. If it spikes suddenly, suspect a large dataset load.

## 4. Configuration and Secret Failures (Exit Code 1)

1. Verify secrets are mounted and not expired:
   ```bash
   kubectl get secret <secret-name> -n <namespace> -o jsonpath='{.metadata.annotations}'
   kubectl exec <pod-name> -n <namespace> -- env | grep -i vault
   ```
2. Check Vault lease expiry if using dynamic secrets (database credentials, API tokens).
3. Verify ConfigMap values match expected schema -- particularly after Helm upgrades.
4. Check if a recent secret rotation in Vault propagated correctly to the pod.

## 5. Dependency Startup Ordering

Trading systems have strict startup dependencies. Verify the dependency chain:

1. **Pricing feed services** must be healthy before OMS or risk engines start.
2. **Database migrations** must complete before application pods accept connections.
3. **Message broker** (Kafka/RabbitMQ) must be available before event consumers start.

Check init container status:
```bash
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.status.initContainerStatuses[*]}'
```

If no init containers enforce ordering, check if the application has retry logic or if it fails fast on missing dependencies.

## 6. Market Hours Impact Assessment

Determine the blast radius immediately:

1. Is the affected service on the **critical trading path** (OMS, pricing, FIX gateway, risk engine)?
2. Is this occurring during **market hours** (pre-market 04:00-09:30, regular 09:30-16:00, post-market 16:00-20:00 ET)?
3. How many replicas remain healthy? Check:
   ```bash
   kubectl get deployment <deployment-name> -n <namespace>
   ```
4. Is the PodDisruptionBudget being violated?
   ```bash
   kubectl get pdb -n <namespace>
   ```

If the service is on the critical trading path during market hours, this is **SEV-1**. Escalate immediately.

## 7. Immediate Remediation

- **OOM**: Increase memory limits as a stopgap. File a follow-up to optimize memory usage.
  ```bash
  kubectl set resources deployment/<name> -n <namespace> --limits=memory=4Gi
  ```
- **Config/Secret**: Restart the Vault agent sidecar or manually sync the secret.
- **Dependency**: Restart the crashing pod after confirming dependencies are healthy.
  ```bash
  kubectl delete pod <pod-name> -n <namespace>
  ```

## 8. Escalation Path

| Severity | Condition | Action |
|----------|-----------|--------|
| SEV-1 | Trading-path service down during market hours | Page on-call SRE + trading desk lead. Open bridge call. |
| SEV-2 | Non-trading service or after-hours trading service | Page on-call SRE. Notify team channel. |
| SEV-3 | Auxiliary service (reporting, backfill) | Create ticket. Fix during business hours. |

## Compliance Note

All remediation actions on trading-path services must be logged in the incident audit trail. Include: who acted, what changed, timestamps, and justification. Regulatory auditors may review pod restart patterns for evidence of system instability during trading windows.
