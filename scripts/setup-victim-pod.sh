#!/usr/bin/env bash
#
# Deploy a "victim" Pod into the local Kind cluster so the SRE
# investigation pipeline has real Kubernetes state to query.
#
# The victim runs ``sleep infinity`` inside a python:3.13-alpine
# container with a low memory limit (64Mi). This means:
#
#   * The Pod stays Ready indefinitely so the K8s investigator can
#     ``kubectl describe`` / ``kubectl get events`` against a real
#     workload.
#   * ``scripts/jam-pod-memory.sh`` can ``kubectl exec`` a memory hog
#     into the container, blow past the limit, and produce a real
#     OOMKilled event + restart loop the pipeline can investigate.
#
# Usage:
#   ./scripts/setup-victim-pod.sh                  # default victim "api-service"
#   ./scripts/setup-victim-pod.sh checkout-api     # custom victim name
#
# Args (optional, positional):
#   $1  victim name      (default: "api-service")
#   $2  namespace        (default: "sentinel-victims")
#   $3  memory limit     (default: "64Mi")
#
# Env:
#   KIND_CLUSTER_NAME    (default: "sentinel-dev" — matches kind-setup.sh)
#
# Pre-reqs:
#   kind + kubectl on PATH; ``./scripts/kind-setup.sh`` already run
#   so the ``sentinel-dev`` cluster is up.
#
set -euo pipefail

VICTIM="${1:-api-service}"
NAMESPACE="${2:-sentinel-victims}"
MEMORY_LIMIT="${3:-64Mi}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-sentinel-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -------------------------------------------------------------------------
# Pre-flight checks
# -------------------------------------------------------------------------

for cmd in kind kubectl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: '$cmd' is not installed. Install it first:" >&2
        echo "  brew install $cmd" >&2
        exit 1
    fi
done

# Bootstrap the Kind cluster + kagent CRDs automatically when missing.
# kind-setup.sh is idempotent so this is safe to chain. Skipped via
# ``SKIP_KIND_BOOTSTRAP=1`` for callers that want explicit control.
if ! kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER_NAME"; then
    if [ "${SKIP_KIND_BOOTSTRAP:-0}" = "1" ]; then
        echo "ERROR: Kind cluster '$KIND_CLUSTER_NAME' not found and " \
             "SKIP_KIND_BOOTSTRAP=1; bring it up with ./scripts/kind-setup.sh" >&2
        exit 1
    fi
    echo ">> Kind cluster '${KIND_CLUSTER_NAME}' not found — bootstrapping via kind-setup.sh"
    "${SCRIPT_DIR}/kind-setup.sh"
fi

KUBE_CONTEXT="kind-${KIND_CLUSTER_NAME}"
echo ">> Using Kind cluster '${KIND_CLUSTER_NAME}' (context ${KUBE_CONTEXT})"

# -------------------------------------------------------------------------
# Namespace
# -------------------------------------------------------------------------

if ! kubectl --context "$KUBE_CONTEXT" get namespace "$NAMESPACE" &>/dev/null; then
    echo ">> Creating namespace '${NAMESPACE}'"
    kubectl --context "$KUBE_CONTEXT" create namespace "$NAMESPACE"
fi

# -------------------------------------------------------------------------
# Deployment + Service
# -------------------------------------------------------------------------

echo ">> Deploying victim '${VICTIM}' (memory limit ${MEMORY_LIMIT})"

kubectl --context "$KUBE_CONTEXT" apply -n "$NAMESPACE" -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${VICTIM}
  labels:
    app: ${VICTIM}
    sentinel.role: victim
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${VICTIM}
  template:
    metadata:
      labels:
        app: ${VICTIM}
        sentinel.role: victim
    spec:
      restartPolicy: Always
      terminationGracePeriodSeconds: 5
      containers:
        - name: ${VICTIM}
          image: python:3.13-alpine
          command: ["sleep", "infinity"]
          resources:
            requests:
              memory: "32Mi"
              cpu: "50m"
            limits:
              memory: "${MEMORY_LIMIT}"
              cpu: "100m"
          # No probes — we want OOMKilled to be the failure mode the
          # investigator catches, not a probe failure.
---
apiVersion: v1
kind: Service
metadata:
  name: ${VICTIM}
  labels:
    app: ${VICTIM}
    sentinel.role: victim
spec:
  selector:
    app: ${VICTIM}
  ports:
    - name: http
      port: 80
      targetPort: 8080
EOF

# -------------------------------------------------------------------------
# Wait for ready
# -------------------------------------------------------------------------

echo ">> Waiting for pod to be Ready (timeout 60s)"
kubectl --context "$KUBE_CONTEXT" wait \
    --for=condition=Ready pod \
    -n "$NAMESPACE" \
    -l "app=${VICTIM}" \
    --timeout=60s

POD_NAME=$(kubectl --context "$KUBE_CONTEXT" get pod -n "$NAMESPACE" \
    -l "app=${VICTIM}" \
    -o jsonpath='{.items[0].metadata.name}')

echo
echo "<< Victim ready"
echo "   namespace : ${NAMESPACE}"
echo "   service   : ${VICTIM}"
echo "   pod       : ${POD_NAME}"
echo "   memory    : ${MEMORY_LIMIT}"
echo
echo ">> Next steps"
echo "   Trigger an OOM:   ./scripts/jam-pod-memory.sh ${VICTIM}"
echo "   Trigger investigation against this victim:"
echo "                     ./scripts/trigger-investigation.sh --victim ${VICTIM}"
