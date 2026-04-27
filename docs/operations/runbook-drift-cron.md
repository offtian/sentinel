# Runbook drift-detection cron (F6.L)

The runbook drift-detection cron sweeps the on-disk runbook catalog daily,
flags regressions against the matcher contract, and pages runbook owners in
Slack so drift gets fixed before it lands in a real investigation.

## What the script does

`scripts/runbook_drift_check.py` runs three sweeps per invocation:

1. **Fixture replay** -- re-runs every runbook's `tests.yaml` fixtures
   through the production matcher (with a deterministic, no-LLM
   disambiguator). Emits `fixture_failure` (matcher returned the wrong
   runbook id) or `min_tag_score_regression` (matcher returned the right
   runbook but with a weaker score).
2. **Stale runbook detection** -- joins each runbook's frontmatter
   `last_validated` against the `runbook_match` table. Emits
   `stale_no_matches` for runbooks past `stale_threshold_days` (default
   90) AND with zero matches in the last `lookback_days` (default 30).
3. **Tools registry validation** -- checks every `tool_name` referenced
   in any runbook's `tools.yaml` against the configured tool registry.
   Emits `tools_yaml_invalid` for runbooks listing missing tool names.

Each detected drift is appended to `runbook_drift_history` (event-grain;
re-detection of an unresolved drift is suppressed at write time so MTTR
math survives across cron ticks). One Slack alert is posted per fresh row.

## How often to run

Daily. The drift signal is slow-moving (runbook authors edit on a
weekly-to-monthly cadence) so a daily sweep is sensitive enough without
the alert-fatigue cost of an hourly cron.

## Scheduling

### cron

```cron
0 6 * * *  cd /opt/sentinel && /usr/bin/just check-runbook-drift >> /var/log/sentinel/drift.log 2>&1
```

### Kubernetes CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: runbook-drift-check
spec:
  schedule: "0 6 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: drift-check
              image: sentinel:latest
              command: ["uv", "run", "python", "scripts/runbook_drift_check.py"]
              envFrom:
                - secretRef:
                    name: sentinel-env
```

### GitHub Actions

```yaml
name: runbook-drift-check

on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: just install
      - run: just check-runbook-drift
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          RUNBOOK_OWNERS_CHANNEL: "#sre-runbook-owners"
```

## When you get paged

1. Open the Slack message. It carries the runbook id, the content_sha
   the drift was detected against, and a one-line suggested fix.
2. Query the `drift_detail` JSON for the structured payload:
   ```sql
   SELECT runbook_id,
          drift_type,
          drift_severity,
          drift_detail,
          detected_at
     FROM runbook_drift_history
    WHERE resolved_at IS NULL
      AND runbook_id = '<runbook_id>'
    ORDER BY detected_at DESC;
   ```
3. Open a follow-up PR against the runbook (the Slack message links to a
   resolution PR template). The PR body should reference the `drift_id`
   so the row can be marked resolved on merge.
4. After the fix lands:
   ```sql
   UPDATE runbook_drift_history
      SET resolved_at = now(),
          resolved_by = '<github-handle>',
          resolution_pr_url = '<pr-url>'
    WHERE drift_id = '<drift_id>';
   ```

## Slack routing

The cron resolves the channel for each drift in this order:

1. The runbook frontmatter `owner` mapped to a per-team channel
   (currently empty -- future leaders extend the override map in
   `application/runbooks/_drift_notifier.py`).
2. The `RUNBOOK_OWNERS_CHANNEL` env var (default `#sre-runbook-owners`).
3. Empty fallback -- drift logged only, no Slack noise.

## Where logs land

The script emits structured `structlog` events:

| Event | Meaning |
|-------|---------|
| `runbook_drift_check_started` | Cron tick start; carries catalog size + Slack-configured flag. |
| `runbook_drift_check_completed` | Cron tick end; carries event_count, fresh_count, deduped_count. |
| `runbook_drift_persisted` | One drift row written to `runbook_drift_history`. |
| `runbook_drift_dedup_skipped` | Open row already covers this drift; no re-write, no Slack alert. |
| `runbook_drift_slack_skipped_unconfigured` | Slack adapter has no token; the cron continues. |
| `runbook_drift_slack_skipped_no_channel` | Owner not in override map and fallback empty. |
| `runbook_drift_slack_posted` | One drift Slack alert sent. |
| `runbook_drift_sweep_tools_skipped` | Tools registry empty; tools sweep no-op. |

## Idempotency

The script is safe to re-run on unchanged state. The dedup gate keys on
`(runbook_id, drift_type, drift_detail)` against open
(`resolved_at IS NULL`) rows -- a second tick on the same drift logs
`runbook_drift_dedup_skipped` and writes nothing. Resolved drift that
re-appears writes a fresh row so the timeline reflects re-detection.
