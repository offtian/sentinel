#!/usr/bin/env bash
#
# Trigger an OOMKilled event on a victim pod by ``kubectl exec``-ing
# a Python memory-hog into it. The container's memory limit is
# breached, the kubelet kills the process, and Kubernetes emits a
# real ``OOMKilled`` event the SRE investigation pipeline can find.
#
# Usage:
#   ./scripts/jam-pod-memory.sh                    # default victim "api-service", 128 MiB hog
#   ./scripts/jam-pod-memory.sh checkout-api 256   # custom victim + hog size
#
# Args (optional, positional):
#   $1  victim name      (default: "api-service")
#   $2  hog size in MiB  (default: 128)
#   $3  namespace        (default: "sentinel-victims")
#
# Env:
#   KIND_CLUSTER_NAME    (default: "sentinel-dev")
#
set -euo pipefail

VICTIM="${1:-api-service}"
HOG_MB="${2:-128}"
NAMESPACE="${3:-sentinel-victims}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-sentinel-dev}"

KUBE_CONTEXT="kind-${KIND_CLUSTER_NAME}"

# -------------------------------------------------------------------------
# Pre-flight
# -------------------------------------------------------------------------

if ! command -v kubectl &>/dev/null; then
    echo "ERROR: 'kubectl' is not installed." >&2
    exit 1
fi

if ! kubectl --context "$KUBE_CONTEXT" get namespace "$NAMESPACE" &>/dev/null; then
    echo "ERROR: namespace '${NAMESPACE}' not found in '${KUBE_CONTEXT}'." >&2
    echo "       Run ./scripts/setup-victim-pod.sh ${VICTIM} first." >&2
    exit 1
fi

POD_NAME=$(kubectl --context "$KUBE_CONTEXT" get pod -n "$NAMESPACE" \
    -l "app=${VICTIM}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$POD_NAME" ]; then
    echo "ERROR: no Pod found with label app=${VICTIM} in namespace ${NAMESPACE}." >&2
    echo "       Run ./scripts/setup-victim-pod.sh ${VICTIM} first." >&2
    exit 1
fi

echo ">> Targeting ${POD_NAME} in namespace ${NAMESPACE}"
echo "   Allocating ${HOG_MB} MiB inside the container — expect OOMKilled"

# -------------------------------------------------------------------------
# Memory hog
# -------------------------------------------------------------------------
#
# Run the allocation in the foreground so kubectl exec exits with the
# kill signal. The bytearray is held by ``time.sleep`` so the kernel
# actually allocates the pages (lazy commit otherwise leaves the limit
# unbreached). 137 = 128 + 9 = SIGKILL exit when the kernel sends one
# (OOMKilled), 0/137 are both expected outcomes; mask any non-fatal
# error so the script keeps going to print events and pod status.
set +e
kubectl --context "$KUBE_CONTEXT" exec -n "$NAMESPACE" "$POD_NAME" -- \
    python -c "import time; b = bytearray(${HOG_MB} * 1024 * 1024); time.sleep(60)"
EXEC_RC=$?
set -e

echo
echo ">> kubectl exec exit code: ${EXEC_RC} (137 = SIGKILL / OOMKilled, 0 = unexpectedly survived)"

# -------------------------------------------------------------------------
# Show the damage
# -------------------------------------------------------------------------

echo
echo ">> Recent events (latest 10):"
kubectl --context "$KUBE_CONTEXT" get events -n "$NAMESPACE" \
    --sort-by=.metadata.creationTimestamp | tail -10

echo
echo ">> Pod status:"
kubectl --context "$KUBE_CONTEXT" get pod -n "$NAMESPACE" -l "app=${VICTIM}" \
    -o wide

echo
echo ">> Last termination reason (if container was killed):"
kubectl --context "$KUBE_CONTEXT" get pod -n "$NAMESPACE" "$POD_NAME" \
    -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}{"\n"}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}' \
    2>/dev/null || echo "(no termination data yet — container may still be restarting)"

echo
echo ">> Now trigger the investigation against this pod:"
echo "   ./scripts/trigger-investigation.sh --victim ${VICTIM}"
