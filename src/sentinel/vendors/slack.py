from __future__ import annotations

from sentinel import _config
from sentinel.utils import logs


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
    target_channel = channel or _config.SRE_SLACK_CHANNEL
    if not target_channel or not _config.SLACK_BOT_TOKEN:
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
        from slack_sdk.web.async_client import AsyncWebClient

        client = AsyncWebClient(token=_config.SLACK_BOT_TOKEN)
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
    target_channel = channel or _config.SUPPORT_SLACK_CHANNEL
    if not target_channel or not _config.SLACK_BOT_TOKEN:
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
        from slack_sdk.web.async_client import AsyncWebClient

        client = AsyncWebClient(token=_config.SLACK_BOT_TOKEN)
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
    confidence_emoji = {"High": ":large_green_circle:", "Medium": ":large_yellow_circle:"}.get(
        confidence_label or "", ":red_circle:"
    )

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


def _build_support_blocks(
    *,
    ticket_key: str,
    ticket_summary: str,
    suggested_response: str,
    confidence_label: str | None,
    category: str | None,
) -> list[dict[str, object]]:
    confidence_emoji = {"High": ":large_green_circle:", "Medium": ":large_yellow_circle:"}.get(
        confidence_label or "", ":red_circle:"
    )

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
