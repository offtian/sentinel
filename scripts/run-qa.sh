#!/usr/bin/env bash
# run-qa.sh — Replaces the Code Reviewer / QA agent role.
# Used as a TeammateIdle hook: runs lint and tests before allowing
# a teammate to go idle. Exit code 2 sends feedback and keeps
# the teammate working.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "=== Asserting runbook content_sha matches frontmatter (F6.E.3) ==="
if ! uv run python scripts/compute_runbook_shas.py --check 2>&1; then
    echo "Runbook content_sha drift detected. Re-run 'uv run python scripts/compute_runbook_shas.py' locally and commit the refreshed shas." >&2
    exit 2
fi

echo "=== Running lint checks ==="
if ! just lint 2>&1; then
    echo "Lint failures found. Fix lint issues before marking work as done." >&2
    exit 2
fi

echo "=== Running unit tests ==="
if ! just test 2>&1; then
    echo "Unit test failures found. Fix failing tests before marking work as done." >&2
    exit 2
fi

echo "All quality checks passed."
exit 0
