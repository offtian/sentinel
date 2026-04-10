#!/bin/bash
# protect-files.sh
# Blocks Edit/Write to protected files, and Bash commands that redirect to them.

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

PROTECTED_PATTERNS=(".env" "package-lock.json" ".git/")

# For Edit/Write: check file_path
if [[ -n "$FILE_PATH" ]]; then
  for pattern in "${PROTECTED_PATTERNS[@]}"; do
    if [[ "$FILE_PATH" == *"$pattern"* ]]; then
      echo "Blocked: $FILE_PATH matches protected pattern '$pattern'" >&2
      exit 2
    fi
  done
fi

# For Bash: check if command writes to a protected file
if [[ "$TOOL_NAME" == "Bash" && -n "$COMMAND" ]]; then
  for pattern in "${PROTECTED_PATTERNS[@]}"; do
    if echo "$COMMAND" | grep -qE "(>|>>|tee|cp|mv|rm)\s.*${pattern}"; then
      echo "Blocked: Bash command targets protected pattern '$pattern'" >&2
      exit 2
    fi
  done
fi

exit 0
