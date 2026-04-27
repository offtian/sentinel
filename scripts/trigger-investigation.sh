#!/usr/bin/env bash
# Trigger a manual SRE investigation against the local API and print a
# Langfuse trace-explorer link so you can inspect the resulting spans.
#
# Usage:
#   ./scripts/trigger-investigation.sh                 # default canned alert
#   ./scripts/trigger-investigation.sh "alert-99" "Disk full" "/var full on web-02" critical disk
#   ./scripts/trigger-investigation.sh --victim api-service             # ensure victim Pod is up + alert against it
#   ./scripts/trigger-investigation.sh --victim api-service --jam       # also OOM the victim before triggering
#
# Flags (consumed before positional args):
#   --victim <name>   Real-pod mode. Calls scripts/setup-victim-pod.sh to
#                     ensure a Pod with this label is running in the local
#                     Kind cluster, then sets the alert's ``service`` field
#                     to <name> so the K8s investigator queries it.
#   --jam [<MiB>]     After ensuring the victim is up, run
#                     scripts/jam-pod-memory.sh to OOMKill it. Default
#                     hog size is 128 MiB. Implies --victim if absent.
#
# Args (all optional, positional):
#   $1  alert id        (default: alert-$(date +%s))
#   $2  title           (default: "High CPU usage" / "OOMKilled pod" in --victim mode)
#   $3  description     (default: "CPU usage exceeded 90% on web-01")
#   $4  severity        (default: "high")  -- low|medium|high|critical
#   $5  service         (default: "api-service" / "<victim>" in --victim mode)
#
# Env:
#   API_URL            (default: http://localhost:8000)
#   LANGFUSE_HOST      (default: http://localhost:3001)
#   KIND_CLUSTER_NAME  (default: sentinel-dev — used by victim helpers)
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
LANGFUSE_HOST="${LANGFUSE_HOST:-http://localhost:3001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -------------------------------------------------------------------------
# Flag parsing — consume named flags before falling through to positional
# -------------------------------------------------------------------------

VICTIM=""
JAM=""
JAM_MB="128"

while [[ $# -gt 0 ]]; do
    case "${1:-}" in
        --victim)
            VICTIM="${2:?--victim requires a name}"
            shift 2
            ;;
        --jam)
            JAM="1"
            # Optional MiB arg may follow; accept only when numeric
            if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
                JAM_MB="$2"
                shift 2
            else
                shift 1
            fi
            ;;
        --)
            shift
            break
            ;;
        --*)
            echo "ERROR: unknown flag '$1'" >&2
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

# --jam without --victim implies the default victim
if [ -n "$JAM" ] && [ -z "$VICTIM" ]; then
    VICTIM="api-service"
fi

# Treat a literal "#" or empty positional as "use default" — guards against
# zsh sessions where `interactive_comments` is off and a trailing inline
# comment (e.g. `./script # demo`) gets parsed into $1.
_arg() { local v="${1-}"; if [ -z "$v" ] || [ "$v" = "#" ]; then printf '%s' "$2"; else printf '%s' "$v"; fi; }

ALERT_ID="$(_arg "${1-}" "alert-$(date +%s)")"

if [ -n "$VICTIM" ]; then
    DEFAULT_TITLE="OOMKilled pod ${VICTIM}"
    DEFAULT_DESCRIPTION="Container ${VICTIM} in namespace sentinel-victims keeps restarting after OOMKilled events."
else
    DEFAULT_TITLE="High CPU usage"
    DEFAULT_DESCRIPTION="CPU usage exceeded 90% on web-01"
fi

TITLE="$(_arg "${2-}" "$DEFAULT_TITLE")"
DESCRIPTION="$(_arg "${3-}" "$DEFAULT_DESCRIPTION")"
SEVERITY="$(_arg "${4-}" "high")"
SERVICE="$(_arg "${5-}" "${VICTIM:-api-service}")"

# -------------------------------------------------------------------------
# Real-pod mode: ensure the victim is up, optionally OOM it first
# -------------------------------------------------------------------------

if [ -n "$VICTIM" ]; then
    echo ">> Real-pod mode: ensuring victim '${VICTIM}' is running"
    "${SCRIPT_DIR}/setup-victim-pod.sh" "$VICTIM"
    echo

    if [ -n "$JAM" ]; then
        echo ">> Jamming ${JAM_MB} MiB into ${VICTIM} (expect OOMKilled)"
        "${SCRIPT_DIR}/jam-pod-memory.sh" "$VICTIM" "$JAM_MB"
        echo
        # Give the kubelet a beat to update pod status / emit events
        # before the investigator runs and queries them.
        sleep 3
    fi
fi

# -------------------------------------------------------------------------
# Trigger investigation
# -------------------------------------------------------------------------

echo ">> Triggering investigation against ${API_URL}"
echo "   alert_id=${ALERT_ID} severity=${SEVERITY} service=${SERVICE}"

response=$(curl -sS -X POST "${API_URL}/api/sre/investigate" \
  -H 'Content-Type: application/json' \
  -d @- <<JSON
{
  "id": "${ALERT_ID}",
  "title": "${TITLE}",
  "description": "${DESCRIPTION}",
  "severity": "${SEVERITY}",
  "service": "${SERVICE}",
  "source": "manual"
}
JSON
)

echo "<< API response:"
echo "${response}" | python3 -m json.tool || echo "${response}"
echo
echo ">> Open Langfuse to view traces:"
echo "   ${LANGFUSE_HOST}/project/default/traces"
echo "   (filter by tag: alert.id=${ALERT_ID})"

if [ -n "$VICTIM" ]; then
    echo
    echo ">> Cleanup when done:"
    echo "   kubectl delete namespace sentinel-victims --context kind-${KIND_CLUSTER_NAME:-sentinel-dev}"
fi
