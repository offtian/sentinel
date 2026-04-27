from __future__ import annotations

from slack_sdk.web.async_client import AsyncWebClient

from sentinel.settings import settings
from sentinel.utils import logs


_CONFIDENCE_EMOJI: dict[str, str] = {
    "High": ":large_green_circle:",
    "Medium": ":large_yellow_circle:",
}
_CONFIDENCE_EMOJI_DEFAULT = ":red_circle:"

_client: AsyncWebClient | None = None


def _get_client() -> AsyncWebClient | None:
    """
    Return a cached Slack client, or None if no token is configured.
    """
    global _client  # noqa: PLW0603
    if _client is None and settings.slack_bot_token:
        _client = AsyncWebClient(token=settings.slack_bot_token)
    return _client


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
    client = _get_client()
    if not target_channel or not client:
        logs.log_event(
            "slack_post_skipped",
            params={"reason": "No channel or token configured"},
        )
        return

    blocks = _build_investigation_blocks(
        alert_id=alert_id,
        alert_title=alert_title,
        root_cause=root_cause,
        remediation=remediation,
        confidence_label=confidence_label,
        findings_summary=findings_summary,
    )

    try:
        await client.chat_postMessage(
            channel=target_channel,
            text=f"Investigation complete for: {alert_title}",
            blocks=blocks,
        )
        logs.log_event(
            "slack_investigation_posted",
            params={"channel": target_channel, "alert_id": alert_id},
        )
    except Exception as e:
        logs.log_exception(e, params={"alert_id": alert_id, "channel": target_channel})


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
    client = _get_client()
    if not target_channel or not client:
        logs.log_event(
            "slack_post_skipped",
            params={"reason": "No channel or token configured"},
        )
        return

    blocks = _build_support_blocks(
        ticket_key=ticket_key,
        ticket_summary=ticket_summary,
        suggested_response=suggested_response,
        confidence_label=confidence_label,
        category=category,
    )

    try:
        await client.chat_postMessage(
            channel=target_channel,
            text=f"Response suggestion for: {ticket_key}",
            blocks=blocks,
        )
        logs.log_event(
            "slack_support_posted",
            params={"channel": target_channel, "ticket_key": ticket_key},
        )
    except Exception as e:
        logs.log_exception(e, params={"ticket_key": ticket_key, "channel": target_channel})


def _build_investigation_blocks(
    *,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> list[dict[str, object]]:
    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Investigation: {alert_title}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Alert ID:* {alert_id}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence:* {confidence_emoji} {confidence_label or 'Unknown'}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root Cause:*\n{root_cause or 'Unable to determine root cause.'}",
            },
        },
    ]

    if remediation:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Remediation:*\n{remediation}"},
            }
        )

    if findings_summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Findings:*\n{findings_summary}"},
            }
        )

    return blocks


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
    client = _get_client()
    if not target_channel or not client:
        logs.log_event(
            "slack_approval_skipped",
            params={"reason": "No channel or token configured"},
        )
        return None

    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Approval Required: {alert_title}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Alert ID:* {alert_id}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence:* {confidence_emoji} {confidence_label or 'Unknown'}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root Cause:*\n{root_cause or 'Unable to determine root cause.'}",
            },
        },
    ]

    if remediation:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Remediation:*\n{remediation}"},
            }
        )

    if findings_summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Findings:*\n{findings_summary}"},
            }
        )

    blocks.append(
        {
            "type": "actions",
            "block_id": f"approval_{investigation_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Publish"},
                    "style": "primary",
                    "action_id": "approve_investigation",
                    "value": investigation_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "reject_investigation",
                    "value": investigation_id,
                },
            ],
        }
    )

    try:
        response = await client.chat_postMessage(
            channel=target_channel,
            text=f"Approval required for investigation: {alert_title}",
            blocks=blocks,
        )
        message_ts = response.get("ts")
        logs.log_event(
            "slack_approval_posted",
            params={
                "channel": target_channel,
                "investigation_id": investigation_id,
                "message_ts": message_ts,
            },
        )
        return message_ts
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"investigation_id": investigation_id, "channel": target_channel},
        )
        return None


_DRIFT_SEVERITY_EMOJI: dict[str, str] = {
    "high": ":red_circle:",
    "medium": ":large_yellow_circle:",
    "low": ":large_blue_circle:",
}


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
    client = _get_client()
    if not client or not channel:
        logs.log_event(
            "runbook_drift_slack_skipped",
            params={
                "reason": "No channel or token configured",
                "runbook_id": runbook_id,
                "drift_type": drift_type,
            },
        )
        return

    blocks = _build_drift_blocks(
        runbook_id=runbook_id,
        content_sha=content_sha,
        drift_type=drift_type,
        drift_severity=drift_severity,
        suggested_fix=suggested_fix,
        resolution_pr_template_url=resolution_pr_template_url,
    )
    fallback_text = f"Runbook drift: {drift_type} on {runbook_id} ({drift_severity})"

    try:
        await client.chat_postMessage(
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
    except Exception as exc:
        logs.log_exception(
            exc,
            params={
                "runbook_id": runbook_id,
                "drift_type": drift_type,
                "channel": channel,
            },
        )


def _build_drift_blocks(
    *,
    runbook_id: str,
    content_sha: str,
    drift_type: str,
    drift_severity: str,
    suggested_fix: str,
    resolution_pr_template_url: str,
) -> list[dict[str, object]]:
    severity_emoji = _DRIFT_SEVERITY_EMOJI.get(drift_severity, ":white_circle:")
    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Runbook Drift: {drift_type}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Runbook:* `{runbook_id}`"},
                {"type": "mrkdwn", "text": f"*Content SHA:* `{content_sha}`"},
                {
                    "type": "mrkdwn",
                    "text": f"*Severity:* {severity_emoji} {drift_severity}",
                },
                {"type": "mrkdwn", "text": f"*Drift type:* {drift_type}"},
            ],
        },
    ]
    if suggested_fix:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Suggested fix:*\n{suggested_fix}"},
            }
        )
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<{resolution_pr_template_url}|Open resolution PR template>",
            },
        }
    )
    return blocks


def _build_support_blocks(
    *,
    ticket_key: str,
    ticket_summary: str,
    suggested_response: str,
    confidence_label: str | None,
    category: str | None,
) -> list[dict[str, object]]:
    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Response Suggestion: {ticket_key}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Ticket:* {ticket_summary}"},
                {"type": "mrkdwn", "text": f"*Category:* {category or 'Unknown'}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence:* {confidence_emoji} {confidence_label or 'Unknown'}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Suggested Response:*\n{suggested_response}"},
        },
    ]
