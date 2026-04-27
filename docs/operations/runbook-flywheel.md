# Runbook gap-detection flywheel (F6.M)

The runbook gap-detection flywheel turns recurring `no_match` outcomes from
the runbook matcher into draft PRs against the catalog. The goal is a
closed-loop authoring workflow: every novel alert that the matcher could not
explain becomes a structured signal — and, if it recurs three times in a
week, an actionable scaffold for the team owner to flesh out.

## How it works

1. The SRE pipeline writes a `runbook_match` row on every match attempt,
   including `match_method = 'no_match'` for novel alerts (F6.D, RFC §3.3).
2. The weekly job (`scripts/runbook_gap_flywheel.py`) walks the no-match
   rows from the last `flywheel_lookback_days` (default 7), joins each one
   with its `alert_request` envelope, and computes a deterministic
   fingerprint:
   ```
   sha256(sorted_alert_labels || classification_category)[:16]
   ```
3. Rows sharing a fingerprint upsert into one `runbook_gap_cluster` row
   (UNIQUE on `fingerprint`). The job tracks `member_count`,
   `flywheel_iteration`, `distinct_services`, and `distinct_alertnames`
   denormalised so the dashboard does not need to scan the JSONB array.
4. Clusters with `member_count >= flywheel_min_cluster_size` (default 3)
   that do not already have an open draft PR get an auto-generated runbook
   skeleton committed to a fresh `flywheel/runbook-gap-<fingerprint>` branch
   and pushed via `gh pr create --draft`.

The skeleton lands at
`src/sentinel/plugins/teams/sre/runbooks/AUTOGEN-<fingerprint>/` and contains
the four-file runbook quartet (`RUNBOOK.md` + `tools.yaml` + `checks.yaml`
+ `tests.yaml`) with explicit `TODO` markers and a sampled fixture seeded
from the cluster's representative alert.

## Configuration

The script reads three knobs off `BaseConfiguration`. Defaults shown.

| Knob | Default | Purpose |
|------|---------|---------|
| `flywheel_lookback_days` | `7` | How far back the no-match query reaches. |
| `flywheel_min_cluster_size` | `3` | Threshold to open a draft PR (lower = noisier; higher = misses signal). |
| `flywheel_pr_template_team` | `sre-platform` | CODEOWNERS team mentioned in the PR body for routing. |

## Running locally (dry-run)

```bash
just run-runbook-flywheel --dry-run
```

Walks the last week's no-matches and logs the clusters it would act on
without writing rows or shelling out to `gh`. Useful for the first
scheduled invocation before authorising the flywheel to write to the
catalog.

## Running in production

```bash
just run-runbook-flywheel
```

Cron snippet (weekly, Mondays at 09:00 UTC):

```cron
0 9 * * 1  cd /opt/sentinel && /usr/bin/just run-runbook-flywheel >> /var/log/sentinel/flywheel.log 2>&1
```

GitHub Actions equivalent (run on a hosted runner with `gh` and write
permissions to the catalog repo):

```yaml
name: runbook-gap-flywheel

on:
  schedule:
    - cron: "0 9 * * 1"
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: just install
      - run: just run-runbook-flywheel
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## Triaging open auto-PRs

1. Query the cluster table for open PRs:
   ```sql
   SELECT fingerprint,
          member_count,
          flywheel_iteration,
          draft_pr_url,
          last_seen_at
     FROM runbook_gap_cluster
    WHERE draft_pr_closed_at IS NULL
      AND draft_pr_url IS NOT NULL
    ORDER BY last_seen_at DESC;
   ```
   The `ix_runbook_gap_cluster_open_prs` partial index makes this query
   cheap even as the historical resolved-PR table grows.

2. For each open PR, the team owner either:
   - Fills in the TODO sections, marks the PR ready, lands it -> the
     runbook joins the catalog and the matcher starts winning the next
     occurrence of this fingerprint.
   - Closes the PR without action when the cluster turns out to be noise.

3. Update the cluster's disposition manually after the PR is closed:
   ```sql
   UPDATE runbook_gap_cluster
      SET draft_pr_closed_at = now(),
          draft_pr_disposition = 'merged'  -- or 'closed_no_action', etc.
    WHERE fingerprint = '<fingerprint>';
   ```
   A future iteration will surface a small CLI / API for this; v1 is
   manual SQL because the disposition vocabulary is small and the volume
   of weekly PRs starts small.

## Disposition vocabulary

The `draft_pr_disposition` CHECK constraint accepts five values:

| Value | Meaning |
|-------|---------|
| `merged` | Author landed the runbook; matcher will hit on next occurrence. |
| `closed_no_action` | Cluster was noise; no runbook needed. |
| `duplicate_of_existing` | An existing runbook already covers this; consider tightening its `applies_to`. |
| `in_review` | PR open but pending discussion. |
| `rejected_low_signal` | Cluster real but coverage not worth a runbook (tracked for trend reporting). |

These map to the closed-loop measurement question "of N auto-PRs, how
many produced a runbook?" — `merged` count divided by total disposition
count is the flywheel's hit rate.

## Chronicity signal

`flywheel_iteration` increments on every weekly re-detection of the
same fingerprint. A cluster that re-fires at iteration 5 with
`disposition='closed_no_action'` from iteration 1 is a recurrent gap
that the team has explicitly declined to address — the metric surfaces
that decision so it can be revisited.
