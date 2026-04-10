#!/usr/bin/env bash
set -euo pipefail

cd "$CLAUDE_PROJECT_DIR"

before_file="$(mktemp)"
after_file="$(mktemp)"
trap 'rm -f "$before_file" "$after_file"' EXIT

git diff --name-only | sort > "$before_file"

just clean
just lint-fix

if ! just lint; then
  cat <<'EOF'
{"decision":"block","reason":"I ran `just clean`, `just lint-fix`, and `just lint`, and `just lint` still fails. Fix the reported issues before stopping."}
EOF
  exit 0
fi

git diff --name-only | sort > "$after_file"

if ! cmp -s "$before_file" "$after_file"; then
  cat <<'EOF'
{"decision":"block","reason":"`just lint-fix` changed files. Re-read the updated files, verify them, and then try stopping again."}
EOF
  exit 0
fi
