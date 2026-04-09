---
name: chart-helm-best-practices
description: Helm chart authoring best practices for chart generation agents
version: 1.0.0
applies_to: ["chart_*", "helm_*"]
---

# Chart and Helm Best Practices

## 1. PodDisruptionBudget Requirements

All trading-path services must have a PodDisruptionBudget to prevent voluntary disruptions during market hours.

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ .Release.Name }}-pdb
spec:
  minAvailable: 2  # Or use maxUnavailable: 1
  selector:
    matchLabels:
      app: {{ .Release.Name }}
```

PDB rules by service tier:

| Service Tier | minAvailable | Rationale |
|-------------|-------------|-----------|
| FIX gateway | 2 | Must maintain active/standby pair |
| Pricing engine | 3 | Minimum for quorum-based consistency |
| OMS | 2 | Must handle orders during rolling updates |
| Risk engine | 2 | Continuous risk calculation required |
| Reporting / batch | 1 | Can tolerate brief disruption |

Never set `maxUnavailable: 100%` on trading-path services. Cluster autoscaler and node drain operations must respect PDBs.

## 2. Node Affinity for Low-Latency Workloads

Pin latency-sensitive workloads to dedicated node pools with performance-optimized instances.

```yaml
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: workload-type
              operator: In
              values:
                - low-latency
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
            - key: topology.kubernetes.io/zone
              operator: In
              values:
                - us-east-1a  # Co-location zone
```

Additional requirements for latency-sensitive pods:
- Set `hostNetwork: true` only if kernel bypass networking (DPDK/SRIOV) is required.
- Use `topologySpreadConstraints` to spread replicas across zones while keeping them close to the exchange co-location.
- Avoid scheduling on nodes running batch workloads. Use taints and tolerations:
  ```yaml
  tolerations:
    - key: "dedicated"
      operator: "Equal"
      value: "low-latency"
      effect: "NoSchedule"
  ```

## 3. Secret Management via Vault

Never store secrets in Helm values files or ConfigMaps. Use the Vault Agent Injector.

```yaml
annotations:
  vault.hashicorp.com/agent-inject: "true"
  vault.hashicorp.com/role: "{{ .Release.Name }}"
  vault.hashicorp.com/agent-inject-secret-db-creds: "secret/data/{{ .Release.Namespace }}/{{ .Release.Name }}/db"
  vault.hashicorp.com/agent-inject-template-db-creds: |
    {{`{{- with secret "secret/data/` }}{{ .Release.Namespace }}{{ `/` }}{{ .Release.Name }}{{ `/db" }}` }}
    {{`POSTGRES_USER={{ .Data.data.username }}`}}
    {{`POSTGRES_PASSWORD={{ .Data.data.password }}`}}
    {{`{{ end }}`}}
```

Secret management rules:
- All database credentials must be dynamic secrets with a maximum TTL of 1 hour.
- API keys for exchange connectivity must use Vault's KV v2 with versioning enabled.
- TLS certificates for FIX sessions must auto-renew via Vault PKI with a 30-day buffer before expiry.
- Never use `envFrom` with secrets -- mount as files and read from the application.

## 4. Resource Requests and Limits

Set explicit resource requests and limits for all containers. Requests determine scheduling; limits prevent noisy neighbors.

```yaml
resources:
  requests:
    cpu: {{ .Values.resources.requests.cpu | default "500m" }}
    memory: {{ .Values.resources.requests.memory | default "512Mi" }}
  limits:
    cpu: {{ .Values.resources.limits.cpu | default "2000m" }}
    memory: {{ .Values.resources.limits.memory | default "2Gi" }}
```

Guidelines by service type:

| Service | CPU Request | Memory Request | Memory Limit | Notes |
|---------|-----------|---------------|-------------|-------|
| Risk engine | 4000m | 8Gi | 12Gi | Large position datasets in memory |
| Pricing engine (JVM) | 2000m | 4Gi | 6Gi | Set heap to 75% of memory limit |
| FIX gateway | 1000m | 512Mi | 1Gi | Low memory, CPU for TLS overhead |
| OMS | 2000m | 2Gi | 4Gi | Moderate, spikes during market open |
| Market data handler | 1000m | 1Gi | 2Gi | Scales with symbol count |

Set CPU limits only if the service is latency-sensitive and you want to avoid throttling unpredictability. For batch workloads, omit CPU limits to allow bursting.

## 5. Blue-Green Deployments for Zero Downtime

Use blue-green deployments for trading-path services to eliminate downtime during releases.

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 100%       # Deploy full green set before removing blue
    maxUnavailable: 0     # Never reduce capacity during rollout
```

For true blue-green with traffic switching, use a service mesh or separate Service objects:

1. Deploy the green Deployment alongside the existing blue.
2. Run health checks and warm-up queries against the green pods.
3. Switch the Service selector to point to green.
4. Keep the blue Deployment running for 15 minutes as a rollback target.
5. Delete the blue Deployment only after confirming green is stable.

Readiness probe must verify the application is fully initialized:
```yaml
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 5
  failureThreshold: 3
```

For JVM services, include a startup probe to allow for class loading and JIT compilation:
```yaml
startupProbe:
  httpGet:
    path: /health/started
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 30  # Up to 150s for JVM warmup
```

## 6. Deployment Windows

Include a deployment window annotation to prevent automated deployments during market hours:

```yaml
metadata:
  annotations:
    deploy.internal/blocked-windows: "mon-fri:0925-1605:America/New_York"
    deploy.internal/emergency-override: "requires-approval"
```

Automated deployment pipelines must check this annotation before proceeding. Emergency deployments during market hours require explicit approval from the trading desk lead and on-call SRE.

## Market Hours Impact

All Helm chart changes for trading-path services should be deployed outside market hours (before 09:25 or after 16:05 ET on trading days). Weekend deployments are preferred for infrastructure-level changes (node pool updates, service mesh upgrades, Vault configuration).
