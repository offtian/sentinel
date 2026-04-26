#!/usr/bin/env bash
# Trigger a manual SRE investigation against the local API and print a
# Langfuse trace-explorer link so you can inspect the resulting spans.
#
# Usage:
#   ./scripts/trigger-investigation.sh                 # default canned alert
#   ./scripts/trigger-investigation.sh "alert-99" "Disk full" "/var full on web-02" critical disk
#
# Args (all optional, positional):
#   $1  alert id        (default: alert-$(date +%s))
#   $2  title           (default: "High CPU usage")
#   $3  description     (default: "CPU usage exceeded 90% on web-01")
#   $4  severity        (default: "high")  -- low|medium|high|critical
#   $5  service         (default: "api-service")
#
# Env:
#   API_URL            (default: http://localhost:8000)
#   LANGFUSE_HOST      (default: http://localhost:3001)
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
LANGFUSE_HOST="${LANGFUSE_HOST:-http://localhost:3001}"

# Treat a literal "#" or empty positional as "use default" — guards against
# zsh sessions where `interactive_comments` is off and a trailing inline
# comment (e.g. `./script # demo`) gets parsed into $1.
_arg() { local v="${1-}"; if [ -z "$v" ] || [ "$v" = "#" ]; then printf '%s' "$2"; else printf '%s' "$v"; fi; }

ALERT_ID="$(_arg "${1-}" "alert-$(date +%s)")"
TITLE="$(_arg "${2-}" "High CPU usage")"
DESCRIPTION="$(_arg "${3-}" "CPU usage exceeded 90% on web-01")"
SEVERITY="$(_arg "${4-}" "high")"
SERVICE="$(_arg "${5-}" "api-service")"

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
