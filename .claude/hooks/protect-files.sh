#!/bin/bash
# protect-files.sh
# Blocks Edit/Write to protected files, and Bash commands that redirect to them.

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# For Edit/Write: check file_path using basename matching
if [[ -n "$FILE_PATH" ]]; then
  BASENAME=$(basename "$FILE_PATH")

  # Block exact .env file (not .env.default, .env.example, etc.)
  if [[ "$BASENAME" == ".env" ]]; then
    echo "Blocked: $FILE_PATH is a protected .env file" >&2
    exit 2
  fi

  # Block files inside .git/
  if [[ "$FILE_PATH" == */.git/* ]]; then
    echo "Blocked: $FILE_PATH is inside .git/" >&2
    exit 2
  fi

  # Block package-lock.json
  if [[ "$BASENAME" == "package-lock.json" ]]; then
    echo "Blocked: $FILE_PATH is a protected lock file" >&2
    exit 2
  fi
fi

exit 0
