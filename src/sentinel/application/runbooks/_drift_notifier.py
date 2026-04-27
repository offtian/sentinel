"""
F6.L.4 — Slack notification helper for runbook drift events.

The cron entry point (``scripts/runbook_drift_check.py``) calls
:func:`notify_drift` after persisting each drift row so the runbook owner
sees the page in Slack the moment the sweep detects regression. Lives in
the application layer because it composes a domain-layer drift event with
a vendor-layer Slack adapter — neither domain nor vendors can own that
composition without violating import-linter contracts.

Routing precedence (first match wins):

1. Runbook frontmatter ``owner`` field that maps to a known team channel
   (``_OWNER_CHANNEL_OVERRIDES`` — wired empty today; future leaders add
   their team mappings here).
2. ``BaseConfiguration.runbook_owners_channel`` fallback.
3. No-op + structured log when the fallback is empty (unowned drift).

No-op contract:

* Slack adapter unconfigured (no ``SLACK_BOT_TOKEN``) → log
  ``runbook_drift_slack_skipped_unconfigured`` and return. Vendor-adapter
  convention: missing credentials never raise.
* Slack ``chat.postMessage`` exception → log via
  :func:`logs.log_exception` and return. One drift's Slack outage must
  never break the rest of the sweep.

The PR template URL is a static repo-relative path the operator can
materialise locally (``.github/PULL_REQUEST_TEMPLATE/runbook_drift.md``);
the script does not yet emit a runtime URL because the resolution-PR
template lands as a follow-on artefact in the same PR series.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from sentinel.data.sql import runbook_drift as drift_sql
from sentinel.domain.runbooks import drift as drift_mod
from sentinel.utils import logs


_RESOLUTION_PR_TEMPLATE_URL = (
    "https://github.com/sentinel/sentinel/blob/main/.github/PULL_REQUEST_TEMPLATE/runbook_drift.md"
)

# Per-team channel overrides keyed by the runbook frontmatter ``owner`` field.
# Empty today — future leaders extend this dict (or surface it via Settings)
# when team-specific routing actually exists.
_OWNER_CHANNEL_OVERRIDES: Mapping[str, str] = {}


# Suggested-fix copy keyed on drift_type. Kept terse so the Slack message
# stays scannable; the JSON detail is the source of truth for the operator.
_SUGGESTED_FIX_BY_TYPE: Mapping[drift_sql.DriftType, str] = {
    "fixture_failure": (
        "Re-run the failing fixture locally; if expected output drifted, "
        "update tests.yaml. Otherwise tighten matcher tags."
    ),
    "min_tag_score_regression": (
        "Inspect renamed/removed tags on the runbook frontmatter. "
        "Lower expected min_tag_score only after confirming the tag rename was intentional."
    ),
    "stale_no_matches": (
        "Either deprecate (set deprecated_at) or refresh last_validated "
        "after manually exercising the runbook against a recent alert."
    ),
    "tools_yaml_invalid": (
        "Update tools.yaml to reference only registered tool names "
        "(or register the missing tool). The matcher would crash on this runbook in production."
    ),
    "content_sha_mismatch": (
        "Run `just check-runbook-shas` locally and commit the regenerated content_sha frontmatter."
    ),
}


class _SlackAdapter(Protocol):
    """
    Minimal contract the drift notifier requires from the Slack vendor.

    Defined as a Protocol so the script can pass either the real
    ``vendors.slack`` module or a test stub without coupling the notifier
    to the exact module shape.
    """

    @property
    def is_configured(self) -> bool:
        """Return True when the adapter has credentials to post messages."""
        ...

    async def post_drift_alert(
        self,
        *,
        channel: str,
        runbook_id: str,
        content_sha: str,
        drift_type: str,
        drift_severity: str,
        suggested_fix: str,
        resolution_pr_template_url: str,
    ) -> None:
        """Post one drift alert message to the given channel. Never raises."""
        ...


def _resolve_channel(*, runbook_owner: str | None, fallback_channel: str) -> str:
    """
    Return the Slack channel for a drift event's runbook owner.

    Empty string return signals "no channel" — caller should skip the
    Slack post entirely (no-op). The split between "owner mapped to a
    team channel" vs "fall back to runbook_owners_channel" stays in
    one helper so future routing rules (e.g. severity-based routing,
    business-hours routing) extend in one place.
    """
    if runbook_owner:
        team_channel = _OWNER_CHANNEL_OVERRIDES.get(runbook_owner)
        if team_channel:
            return team_channel
    return fallback_channel


async def notify_drift(
    *,
    event: drift_mod.DriftEvent,
    runbook_owner: str | None,
    slack_adapter: _SlackAdapter,
    fallback_channel: str,
) -> None:
    """
    Post one Slack message for the drift ``event``. Never raises.

    Failure modes (all return None silently after a structured log):

    * Slack adapter unconfigured → ``runbook_drift_slack_skipped_unconfigured``
    * No channel resolved (owner unmapped + empty fallback) →
      ``runbook_drift_slack_skipped_no_channel``
    * Slack adapter raises → :func:`logs.log_exception`

    :param event: The drift event just persisted (the dedup gate has
        already passed; this is a fresh page-worthy drift).
    :param runbook_owner: ``RunbookMetadata.owner`` value the cron pulled
        from the runbook frontmatter. ``None`` when the owner field is
        unset (loader treats blank owner as None — drift posts go to the
        fallback channel only).
    :param slack_adapter: Vendor adapter exposing the
        :class:`_SlackAdapter` Protocol surface.
    :param fallback_channel: Channel resolved off
        ``BaseConfiguration.runbook_owners_channel``; passed in
        explicitly so the notifier stays config-free and tests can
        construct one without get_config().
    """
    if not slack_adapter.is_configured:
        logs.log_event(
            "runbook_drift_slack_skipped_unconfigured",
            params={
                "runbook_id": event.runbook_id,
                "drift_type": event.drift_type,
            },
        )
        return

    channel = _resolve_channel(
        runbook_owner=runbook_owner,
        fallback_channel=fallback_channel,
    )
    if not channel:
        logs.log_event(
            "runbook_drift_slack_skipped_no_channel",
            params={
                "runbook_id": event.runbook_id,
                "drift_type": event.drift_type,
                "runbook_owner": runbook_owner,
            },
        )
        return

    suggested_fix = _SUGGESTED_FIX_BY_TYPE.get(event.drift_type, "")
    try:
        await slack_adapter.post_drift_alert(
            channel=channel,
            runbook_id=event.runbook_id,
            content_sha=event.runbook_content_sha,
            drift_type=event.drift_type,
            drift_severity=event.drift_severity,
            suggested_fix=suggested_fix,
            resolution_pr_template_url=_RESOLUTION_PR_TEMPLATE_URL,
        )
    except Exception as exc:
        # One drift's Slack outage must never break the rest of the sweep.
        # Log via log_exception so Sentry picks it up; return silently so
        # the cron carries on with the next drift event.
        logs.log_exception(
            exc,
            params={
                "runbook_id": event.runbook_id,
                "drift_type": event.drift_type,
                "channel": channel,
            },
        )
