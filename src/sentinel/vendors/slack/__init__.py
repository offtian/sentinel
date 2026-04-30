from __future__ import annotations

from sentinel.settings import settings
from sentinel.utils import logs
from sentinel.vendors.slack import _blocks as _slack_blocks
from sentinel.vendors.slack import _client as _slack_client
from sentinel.vendors.slack._blocks import (
    approval_request_blocks,
    drift_alert_blocks,
    investigation_summary_blocks,
    support_summary_blocks,
)
from sentinel.vendors.slack._client import AsyncSlackClient, SlackClientError, get_client
from sentinel.vendors.slack._parsers import (
    MentionEvent,
    MessageEvent,
    parse_mention_event,
    parse_message_event,
)


async def post_investigation_summary(
    *,
    channel: str | None = None,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> None:
    """
    Post an investigation summary to a Slack channel.

    Uses Slack Web API to send a formatted message with investigation results.
    """
    target_channel = channel or settings.sre_slack_channel
    if not target_channel or not _slack_client.get_client().is_configured:
        logs.log_event(
            "slack_post_skipped",
            params={"reason": "No channel or token configured"},
        )
        return

    slack = _slack_client.get_client()
    blocks = _slack_blocks.investigation_summary_blocks(
        alert_id=alert_id,
        alert_title=alert_title,
        root_cause=root_cause,
        remediation=remediation,
        confidence_label=confidence_label,
        findings_summary=findings_summary,
    )

    try:
        await slack.post_message(
            channel=target_channel,
            text=f"Investigation complete for: {alert_title}",
            blocks=blocks,
        )
        logs.log_event(
            "slack_investigation_posted",
            params={"channel": target_channel, "alert_id": alert_id},
        )
    except _slack_client.SlackClientError as exc:
        logs.log_exception(exc, params={"alert_id": alert_id, "channel": target_channel})


async def post_support_suggestion(
    *,
    channel: str | None = None,
    ticket_key: str,
    ticket_summary: str,
    suggested_response: str,
    confidence_label: str | None,
    category: str | None,
) -> None:
    """Post a support response suggestion to a Slack channel."""
    target_channel = channel or settings.support_slack_channel
    if not target_channel or not _slack_client.get_client().is_configured:
        logs.log_event(
            "slack_post_skipped",
            params={"reason": "No channel or token configured"},
        )
        return

    slack = _slack_client.get_client()
    blocks = _slack_blocks.support_summary_blocks(
        ticket_key=ticket_key,
        ticket_summary=ticket_summary,
        suggested_response=suggested_response,
        confidence_label=confidence_label,
        category=category,
    )

    try:
        await slack.post_message(
            channel=target_channel,
            text=f"Response suggestion for: {ticket_key}",
            blocks=blocks,
        )
        logs.log_event(
            "slack_support_posted",
            params={"channel": target_channel, "ticket_key": ticket_key},
        )
    except _slack_client.SlackClientError as exc:
        logs.log_exception(exc, params={"ticket_key": ticket_key, "channel": target_channel})


async def post_approval_request(
    *,
    channel: str | None = None,
    investigation_id: str,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> str | None:
    """
    Post an investigation summary with Approve/Reject buttons to Slack.

    Return the message timestamp (``ts``) for tracking, or None if posting was skipped.
    """
    target_channel = channel or settings.sre_slack_channel
    if not target_channel or not _slack_client.get_client().is_configured:
        logs.log_event(
            "slack_approval_skipped",
            params={"reason": "No channel or token configured"},
        )
        return None

    slack = _slack_client.get_client()
    blocks = _slack_blocks.approval_request_blocks(
        investigation_id=investigation_id,
        alert_id=alert_id,
        alert_title=alert_title,
        root_cause=root_cause,
        remediation=remediation,
        confidence_label=confidence_label,
        findings_summary=findings_summary,
    )

    try:
        ts = await slack.post_message(
            channel=target_channel,
            text=f"Approval required for investigation: {alert_title}",
            blocks=blocks,
        )
        logs.log_event(
            "slack_approval_posted",
            params={
                "channel": target_channel,
                "investigation_id": investigation_id,
                "message_ts": ts,
            },
        )
        return ts
    except _slack_client.SlackClientError as exc:
        logs.log_exception(
            exc,
            params={"investigation_id": investigation_id, "channel": target_channel},
        )
        return None


def is_slack_configured() -> bool:
    """
    Return True when a Slack bot token is present in settings.

    Mirrors the vendor-adapter ``is_configured`` convention so callers
    (notably :mod:`sentinel.application.runbooks._drift_notifier`) can
    short-circuit before constructing payloads.
    """
    return bool(settings.slack_bot_token)


async def post_drift_alert(
    *,
    channel: str,
    runbook_id: str,
    content_sha: str,
    drift_type: str,
    drift_severity: str,
    suggested_fix: str,
    resolution_pr_template_url: str,
) -> None:
    """
    Post one runbook drift alert to ``channel``. Never raises.

    Used by the F6.L drift-detection cron after persisting each
    ``runbook_drift_history`` row. Returns silently when the Slack client
    is unconfigured (per the vendor-adapter no-op convention) and on any
    Slack API error (one drift's outage must never block the rest of the
    sweep).
    """
    if not _slack_client.get_client().is_configured or not channel:
        logs.log_event(
            "runbook_drift_slack_skipped",
            params={
                "reason": "No channel or token configured",
                "runbook_id": runbook_id,
                "drift_type": drift_type,
            },
        )
        return

    slack = _slack_client.get_client()
    blocks = _slack_blocks.drift_alert_blocks(
        runbook_id=runbook_id,
        content_sha=content_sha,
        drift_type=drift_type,
        drift_severity=drift_severity,
        suggested_fix=suggested_fix,
        resolution_pr_template_url=resolution_pr_template_url,
    )
    fallback_text = f"Runbook drift: {drift_type} on {runbook_id} ({drift_severity})"

    try:
        await slack.post_message(
            channel=channel,
            text=fallback_text,
            blocks=blocks,
        )
        logs.log_event(
            "runbook_drift_slack_posted",
            params={
                "channel": channel,
                "runbook_id": runbook_id,
                "drift_type": drift_type,
                "drift_severity": drift_severity,
            },
        )
    except _slack_client.SlackClientError as exc:
        logs.log_exception(
            exc,
            params={
                "runbook_id": runbook_id,
                "drift_type": drift_type,
                "channel": channel,
            },
        )


__all__ = [
    "AsyncSlackClient",
    "MentionEvent",
    "MessageEvent",
    "SlackClientError",
    "approval_request_blocks",
    "drift_alert_blocks",
    "get_client",
    "investigation_summary_blocks",
    "is_slack_configured",
    "parse_mention_event",
    "parse_message_event",
    "post_approval_request",
    "post_drift_alert",
    "post_investigation_summary",
    "post_support_suggestion",
    "support_summary_blocks",
]
