#!/usr/bin/env bash
# validate-task.sh — Replaces the Product Manager agent role.
# Used as a TaskCompleted hook: validates that completed tasks
# meet basic quality gates. Exit code 2 rejects the completion
# and sends feedback to the teammate.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

ERRORS=()

# Check for uncommitted changes that should have been included
if [ -n "$(git diff --name-only src/ tests/ 2>/dev/null)" ]; then
    ERRORS+=("Unstaged changes in src/ or tests/. Stage and commit your work before marking the task complete.")
fi

# Run a fast lint check (ruff only, not full mypy)
if ! uv run ruff check src/ tests/ 2>&1; then
    ERRORS+=("Ruff check failed. Fix lint issues before completing the task.")
fi

# Run unit tests
if ! just test --tb=short -q 2>&1; then
    ERRORS+=("Unit tests are failing. Fix before completing the task.")
fi

if [ ${#ERRORS[@]} -gt 0 ]; then
    echo "Task completion blocked:" >&2
    for err in "${ERRORS[@]}"; do
        echo "  - $err" >&2
    done
    exit 2
fi

echo "Task validation passed."
exit 0
