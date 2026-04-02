#!/usr/bin/env bash
# Pre-edit hook: require a plan file for feature branches.
# Reads stdin JSON from Claude Code (tool_input with file_path).

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

# Only enforce on feat/ or feature/ branches
case "$branch" in
  feat/*|feature/*)
    # Strip the prefix to get the plan name
    plan_name="${branch#feat/}"
    plan_name="${plan_name#feature/}"
    plan_file="docs/plans/${plan_name}.md"

    if [ ! -f "$plan_file" ]; then
      cat <<EOF
{"decision":"block","reason":"No plan file found for branch '${branch}'. Create ${plan_file} from the template before editing code:\n\ncp docs/plans/_template.md ${plan_file}"}
EOF
      exit 0
    fi
    ;;
esac
