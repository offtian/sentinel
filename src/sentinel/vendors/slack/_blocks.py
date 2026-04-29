from __future__ import annotations


_CONFIDENCE_EMOJI: dict[str, str] = {
    "High": ":large_green_circle:",
    "Medium": ":large_yellow_circle:",
}
_CONFIDENCE_EMOJI_DEFAULT = ":red_circle:"

_DRIFT_SEVERITY_EMOJI: dict[str, str] = {
    "high": ":red_circle:",
    "medium": ":large_yellow_circle:",
    "low": ":large_blue_circle:",
}


def investigation_summary_blocks(
    *,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for an investigation summary."""
    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Investigation: {alert_title[:140]}"},
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
                "text": f"*Root Cause:*\n{root_cause or '_Unable to determine._'}",
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


def approval_request_blocks(
    *,
    investigation_id: str,
    alert_id: str,
    alert_title: str,
    root_cause: str | None,
    remediation: str | None,
    confidence_label: str | None,
    findings_summary: str,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for an approval request with Approve/Reject buttons."""
    blocks = investigation_summary_blocks(
        alert_id=alert_id,
        alert_title=f"Approval Required: {alert_title}",
        root_cause=root_cause,
        remediation=remediation,
        confidence_label=confidence_label,
        findings_summary=findings_summary,
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
    return blocks


def support_summary_blocks(
    *,
    ticket_key: str,
    ticket_summary: str,
    suggested_response: str,
    confidence_label: str | None,
    category: str | None,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for a support response suggestion."""
    confidence_emoji = _CONFIDENCE_EMOJI.get(confidence_label or "", _CONFIDENCE_EMOJI_DEFAULT)

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Response Suggestion: {ticket_key}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Ticket:* {ticket_summary[:200]}"},
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


def drift_alert_blocks(
    *,
    runbook_id: str,
    content_sha: str,
    drift_type: str,
    drift_severity: str,
    suggested_fix: str,
    resolution_pr_template_url: str,
) -> list[dict[str, object]]:
    """Return Block Kit blocks for a runbook drift alert."""
    severity_emoji = _DRIFT_SEVERITY_EMOJI.get(drift_severity, ":white_circle:")

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Runbook Drift: {drift_type}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Runbook:* `{runbook_id}`"},
                {"type": "mrkdwn", "text": f"*Content SHA:* `{content_sha}`"},
                {"type": "mrkdwn", "text": f"*Severity:* {severity_emoji} {drift_severity}"},
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
