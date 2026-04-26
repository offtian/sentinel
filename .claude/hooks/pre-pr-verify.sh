#!/usr/bin/env bash
# Pre-PR-creation gate: when the user is about to run `gh pr create`,
# run the same lint / format / test suite that used to fire on every Stop.
# Otherwise this hook is a silent no-op.
set -euo pipefail

# Tool input is delivered to PreToolUse hooks as JSON on stdin.
input_json="$(cat)"

# Extract the command string. Bail silently if jq is unavailable or the
# JSON shape is unexpected.
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

command_str="$(printf '%s' "$input_json" | jq -r '.tool_input.command // ""' 2>/dev/null || true)"

# Only fire for `gh pr create ...` invocations.
if [[ "$command_str" != *"gh pr create"* ]]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

just clean
just lint-fix

if ! just lint; then
  cat <<'EOF'
{"decision":"block","reason":"`just lint` failed. Fix lint errors before opening the PR."}
EOF
  exit 0
fi

if ! just test; then
  cat <<'EOF'
{"decision":"block","reason":"`just test` failed. Fix test failures before opening the PR."}
EOF
  exit 0
fi
