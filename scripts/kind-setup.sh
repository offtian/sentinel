#!/usr/bin/env bash
#
# Create a Kind cluster for local kagent development.
#
# Installs placeholder kagent CRDs so the adapter can be tested
# against a real K8s API server without the full kagent operator.
#
# Usage:
#   ./scripts/kind-setup.sh
#   just kagent-dev-up
#
set -euo pipefail

CLUSTER_NAME="sentinel-dev"
CRD_NAMESPACE="kagent-system"

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

# -------------------------------------------------------------------------
# Cluster
# -------------------------------------------------------------------------

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    echo "Kind cluster '$CLUSTER_NAME' already exists — skipping creation."
else
    echo "Creating Kind cluster '$CLUSTER_NAME'..."
    kind create cluster --name "$CLUSTER_NAME" --wait 60s
fi

kubectl cluster-info --context "kind-${CLUSTER_NAME}"

# -------------------------------------------------------------------------
# Namespace
# -------------------------------------------------------------------------

if kubectl get namespace "$CRD_NAMESPACE" &>/dev/null; then
    echo "Namespace '$CRD_NAMESPACE' already exists."
else
    echo "Creating namespace '$CRD_NAMESPACE'..."
    kubectl create namespace "$CRD_NAMESPACE"
fi

# -------------------------------------------------------------------------
# Kagent CRD (placeholder — the real CRD ships with the kagent operator)
# -------------------------------------------------------------------------

echo "Applying kagent Investigation CRD..."
kubectl apply -f - <<'EOF'
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: investigations.kagent.dev
spec:
  group: kagent.dev
  names:
    kind: Investigation
    listKind: InvestigationList
    plural: investigations
    singular: investigation
    shortNames:
      - inv
  scope: Namespaced
  versions:
    - name: v1alpha1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              properties:
                alert_id:
                  type: string
                service:
                  type: string
                severity:
                  type: string
                description:
                  type: string
                namespace:
                  type: string
            status:
              type: object
              properties:
                phase:
                  type: string
                  enum:
                    - Pending
                    - Running
                    - Completed
                    - Failed
                result:
                  type: object
                  properties:
                    findings:
                      type: array
                      items:
                        type: object
                        properties:
                          source:
                            type: string
                          summary:
                            type: string
                          raw_data:
                            type: string
                          relevance:
                            type: number
                    sources_queried:
                      type: array
                      items:
                        type: string
      subresources:
        status: {}
      additionalPrinterColumns:
        - name: Phase
          type: string
          jsonPath: .status.phase
        - name: Service
          type: string
          jsonPath: .spec.service
        - name: Age
          type: date
          jsonPath: .metadata.creationTimestamp
EOF

echo ""
echo "=========================================="
echo "  Kind cluster '$CLUSTER_NAME' is ready"
echo "=========================================="
echo ""
echo "To use kagent locally, set these env vars:"
echo ""
echo "  export K8S_INVESTIGATION_BACKEND=kagent"
echo "  export KAGENT_NAMESPACE=${CRD_NAMESPACE}"
echo "  export KUBECONFIG=\$(kind get kubeconfig-path --name=${CLUSTER_NAME} 2>/dev/null || echo \$HOME/.kube/config)"
echo ""
echo "Verify CRD is installed:"
echo "  kubectl get crd investigations.kagent.dev"
echo ""
echo "Create a test investigation:"
echo "  kubectl apply -f - <<YAML"
echo "  apiVersion: kagent.dev/v1alpha1"
echo "  kind: Investigation"
echo "  metadata:"
echo "    name: test-inv"
echo "    namespace: ${CRD_NAMESPACE}"
echo "  spec:"
echo "    alert_id: TEST-1"
echo "    service: my-service"
echo "    severity: high"
echo "    description: Test investigation"
echo "  YAML"
echo ""
echo "Tear down with: just kagent-dev-down"
