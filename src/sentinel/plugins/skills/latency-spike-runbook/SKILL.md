---
name: latency-spike-runbook
description: Procedure for triaging latency spikes and identifying performance regressions
version: 1.0.0
applies_to: ["latency_*", "performance_*"]
---

# Latency Spike Runbook

## 1. Determine the Affected Service and SLA

Identify which service is experiencing elevated latency and its SLA:

| Service | p99 SLA | Critical Threshold |
|---------|---------|-------------------|
| FIX gateway (order routing) | < 5ms | > 20ms |
| Market data feed processing | < 2ms | > 10ms |
| Risk calculation engine | < 500ms | > 2s |
| Order management system | < 10ms | > 50ms |
| Pricing engine | < 1ms | > 5ms |

Check current latency percentiles in Datadog:
```
trace.http.request.duration{service:<service-name>} by {resource_name}.percentile(p50,p95,p99)
```

## 2. Market Data Feed Latency

Stale quotes lead to mispriced trades. This is the highest-priority latency issue.

1. Check feed handler lag:
   ```
   market_data.feed.lag_ms{feed:<feed-name>} by {symbol_group}
   ```
2. Compare timestamps between feed source and internal quote cache.
3. Check if the issue is isolated to one feed provider or affects all feeds.
4. Verify network path between feed handlers and the exchange co-location:
   ```bash
   kubectl exec <feed-handler-pod> -- ping -c 10 <exchange-gateway-ip>
   kubectl exec <feed-handler-pod> -- traceroute <exchange-gateway-ip>
   ```
5. Check if the feed handler is falling behind on message processing:
   ```
   market_data.feed.queue_depth{service:feed-handler}
   ```

If quotes are stale by more than 100ms during market hours, notify the trading desk immediately.

## 3. FIX Gateway Response Times

1. Check FIX session status and message throughput:
   ```
   fix.session.latency_ms{session:<session-id>} by {msg_type}
   fix.session.messages_per_second{session:<session-id>}
   ```
2. Check if the issue is on the inbound (execution reports) or outbound (new orders) path.
3. Verify TCP connection health to the broker/exchange:
   ```bash
   kubectl exec <fix-gateway-pod> -- ss -ti dst <broker-ip>
   ```
4. Check for FIX sequence number gaps indicating message retransmission.

## 4. Garbage Collection Pauses (JVM Services)

Pricing engines and risk calculators often run on JVM. GC pauses cause latency spikes.

1. Check GC metrics:
   ```
   jvm.gc.pause.duration{service:<service-name>} by {gc_cause}
   jvm.gc.pause.count{service:<service-name>}
   jvm.heap.used{service:<service-name>} by {heap_area}
   ```
2. Look for full GC events (stop-the-world pauses > 200ms).
3. Check if heap usage is trending toward the max, indicating memory pressure.
4. Pull GC logs from the pod:
   ```bash
   kubectl logs <pod-name> -n <namespace> | grep "GC pause"
   ```

Common fixes:
- Increase heap size if consistently above 80% utilization.
- Switch to ZGC or Shenandoah for latency-sensitive services.
- Check for object allocation hotspots in recent deployments.

## 5. Network Partition Detection

Trading infrastructure often spans multiple availability zones or co-location sites.

1. Check inter-zone latency:
   ```
   network.latency.ms{source_zone:*,dest_zone:*}
   ```
2. Verify no packet loss between trading zones:
   ```bash
   kubectl exec <pod> -- mtr -r -c 100 <target-ip>
   ```
3. Check if a recent infrastructure change (security group, NLB, service mesh config) is causing asymmetric routing.
4. Verify Kubernetes node-to-node connectivity across zones:
   ```bash
   kubectl get nodes -o wide  # check zones
   ```

## 6. Recent Deployment Correlation

1. Check if the latency spike correlates with a deployment:
   ```bash
   kubectl rollout history deployment/<name> -n <namespace>
   ```
2. Query Datadog deployment events:
   ```
   events("deployment").rollup(count).by(service)
   ```
3. If correlated, review the diff for: new database queries, changed serialization, added middleware, or logging changes.
4. Roll back if the regression is confirmed and market hours are active:
   ```bash
   kubectl rollout undo deployment/<name> -n <namespace>
   ```

## 7. Immediate Remediation

| Root Cause | Action |
|-----------|--------|
| Market data feed lag | Restart feed handler; failover to backup feed if available |
| FIX gateway latency | Check broker connectivity; restart FIX session if sequence is clean |
| GC pauses | Increase heap or restart pod to clear accumulated garbage |
| Network partition | Reroute traffic to healthy zone; engage network team |
| Recent deployment | Roll back deployment |

## Market Hours Impact Assessment

1. Is the latency affecting **order execution quality**? Check fill rates and slippage.
2. Are **risk limits being calculated on stale data**? This could allow positions to exceed limits.
3. Is the **market data feed stale**? Stale quotes can trigger erroneous trades or halt strategies.

During market hours, any latency exceeding the critical threshold on the trading path is **SEV-1**.

## Escalation Path

| Severity | Condition | Action |
|----------|-----------|--------|
| SEV-1 | Trading-path latency above critical threshold during market hours | Page on-call SRE + trading desk + quant team lead |
| SEV-2 | Non-trading latency or after-hours trading latency | Page on-call SRE |
| SEV-3 | Batch processing latency (reporting, backfill) | Create ticket |
