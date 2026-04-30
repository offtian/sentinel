from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from slack_bolt.context.ack.async_ack import AsyncAck

from sentinel import config as config_mod
from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs import common, support_review
from sentinel.interfaces.graphs._archive import investigation
from sentinel.interfaces.graphs.agents import intent_router, k8s_runner
from sentinel.interfaces.graphs.agents import utils as agent_utils
from sentinel.interfaces.slack import constants as slack_constants
from sentinel.interfaces.slack.app import app
from sentinel.interfaces.slack.status_update import SlackStatusUpdateClient
from sentinel.interfaces.workflows import sre_investigation as workflows_sre_investigation
from sentinel.settings import settings
from sentinel.utils import logs
from sentinel.vendors.slack import _blocks as slack_blocks
from sentinel.vendors.slack import _parsers as slack_parsers


def _envelope_for_slack(*, channel: str, user_id: str) -> envelope_mod.Envelope:
    """
    Mint an Envelope for a Slack-driven pipeline run.

    Slack mentions are an interactive surface, not a webhook with upstream
    correlation IDs, so we mint a fresh envelope per invocation. ``tenant_id``
    encodes the Slack channel so multi-team workspaces can audit by channel.
    """
    return envelope_mod.Envelope(
        request_id=uuid.uuid4(),
        tenant_id=f"slack:{channel}",
        cluster_id="slack",
        region="slack",
        pii_class="internal",
        received_at=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# LLM-based intent routing
# ---------------------------------------------------------------------------


async def _classify_intent(text: str) -> intent_router.Intent:
    """Route user message to SRE or Support via the intent router agent."""
    cfg = config_mod.get_config()
    router_agent = cfg.agent_for("intent_router")
    agent_utils.set_agent_span_attributes(
        prompt_sha256=intent_router.PROMPT_SHA256,
        model_name=agent_utils.get_model_name(router_agent),
        agent_name="intent_router",
    )
    result = await router_agent.run(
        user_prompt=text,
        deps=intent_router.Dependencies(message=text),
    )
    return result.output.intent  # type: ignore[no-any-return]


def _strip_mention(text: str) -> str:
    """Remove <@UXXXXXXX> bot mentions from the message text."""
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


# ---------------------------------------------------------------------------
# Block Kit formatters
# ---------------------------------------------------------------------------


def _investigation_blocks(
    reply: common.InvestigationReply,
    alert_title: str,
) -> list[dict[str, object]]:
    confidence_label = reply.confidence.label.value if reply.confidence else "Unknown"
    confidence_emoji = {
        "High": ":large_green_circle:",
        "Medium": ":large_yellow_circle:",
    }.get(confidence_label, ":red_circle:")

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Investigation: {alert_title[:140]}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Alert ID:* {reply.alert_id}"},
                {"type": "mrkdwn", "text": f"*Confidence:* {confidence_emoji} {confidence_label}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root Cause:*\n{reply.root_cause or '_Unable to determine._'}",
            },
        },
    ]

    if reply.remediation:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Remediation:*\n{reply.remediation}"},
            }
        )

    if reply.findings_summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Findings:*\n{reply.findings_summary}"},
            }
        )

    return blocks


def _investigation_blocks_from_outcome(
    outcome: workflows_sre_investigation.InvestigationOutcome,
    alert_title: str,
) -> list[dict[str, object]]:
    """Build Block Kit blocks from a LangGraph InvestigationOutcome."""
    confidence_label = outcome.confidence.label.value if outcome.confidence else "Unknown"
    confidence_emoji = {
        "High": ":large_green_circle:",
        "Medium": ":large_yellow_circle:",
    }.get(confidence_label, ":red_circle:")

    approval_note = ""
    if outcome.needs_approval:
        approval_note = " _(pending approval)_"

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Investigation: {alert_title[:140]}"},
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Category:* {outcome.classification_category or 'unknown'}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence:* {confidence_emoji} {confidence_label}{approval_note}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root Cause:*\n{outcome.root_cause or '_Unable to determine._'}",
            },
        },
    ]

    if outcome.remediation:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Remediation:*\n{outcome.remediation}"},
            }
        )

    return blocks


def _support_blocks(
    reply: common.SupportReply,
    ticket_summary: str,
) -> list[dict[str, object]]:
    confidence_label = reply.confidence.label.value if reply.confidence else "Unknown"
    confidence_emoji = {
        "High": ":large_green_circle:",
        "Medium": ":large_yellow_circle:",
    }.get(confidence_label, ":red_circle:")

    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Sentinel Response Suggestion"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Query:* {ticket_summary[:200]}"},
                {"type": "mrkdwn", "text": f"*Category:* {reply.category or 'Unknown'}"},
                {"type": "mrkdwn", "text": f"*Confidence:* {confidence_emoji} {confidence_label}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Suggested Response:*\n{reply.suggested_response}",
            },
        },
    ]

    sources = reply.sources or []
    if sources:
        source_lines = "\n".join(
            f"• <{s['url']}|{s['title']}>" if s.get("url") else f"• {s['title']}"
            for s in sources[:5]
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Sources:*\n{source_lines}"},
            }
        )

    return blocks


# ---------------------------------------------------------------------------
# Core pipeline runners
# ---------------------------------------------------------------------------


async def _run_sre(
    text: str,
    *,
    client: Any,
    channel: str,
    thread_ts: str,
) -> None:
    status = SlackStatusUpdateClient(client=client, channel=channel, thread_ts=thread_ts)

    # Build a minimal Alert from the Slack message text
    first_line = text.split("\n")[0][:200]
    alert = alert_entities.Alert(
        id=f"slack-{thread_ts}",
        source="manual",
        title=first_line or "Alert from Slack",
        description=text,
        severity=alert_entities.AlertSeverity.MEDIUM,
        service="unknown",
        triggered_at=datetime.now(tz=UTC),
        raw_payload={"slack_text": text},
    )

    envelope = _envelope_for_slack(channel=channel, user_id="slack-thread")

    if settings.langgraph_sre_enabled:
        # MemorySaver: ephemeral, sufficient for the Slack conversational surface
        # which has no cross-restart resume requirement.
        graph = workflows_sre_investigation.build_sre_investigation_graph(
            checkpointer=MemorySaver(),
        )
        outcome = await workflows_sre_investigation.investigate_alert(
            alert=alert,
            envelope=envelope,
            graph=graph,
        )
        blocks = _investigation_blocks_from_outcome(outcome, alert.title)
    else:
        cfg = config_mod.get_config()
        reply = await investigation.investigate_alert(
            alert=alert,
            envelope=envelope,
            agent_for=cfg.agent_for,
            status_update_client=status,
            post_to_slack=False,  # we post directly in this thread instead
            k8s_adapter=cfg.build_k8s_investigation_adapter(
                agent_runner=k8s_runner.run_k8s_agent,
            ),
            challenger_adapter=cfg.build_challenger_adapter(),
        )
        blocks = _investigation_blocks(reply, alert.title)

    blocks = slack_blocks.investigation_summary_blocks(
        alert_id=reply.alert_id,
        alert_title=alert.title,
        root_cause=reply.root_cause,
        remediation=reply.remediation,
        confidence_label=reply.confidence.label.value if reply.confidence else None,
        findings_summary=reply.findings_summary or "",
    )
    await status.replace_with_result(
        text=f"Investigation complete: {alert.title}",
        blocks=blocks,
    )

    logs.log_event(
        slack_constants.EVENT_SRE_COMPLETE,
        params={"alert_id": alert.id, "channel": channel},
    )


async def _run_support(
    text: str,
    *,
    client: Any,
    channel: str,
    thread_ts: str,
    user_id: str,
) -> None:
    status = SlackStatusUpdateClient(client=client, channel=channel, thread_ts=thread_ts)

    ticket = support_entities.Ticket(
        id=f"slack-{thread_ts}",
        key=f"SLACK-{thread_ts[:8]}",
        summary=text.split("\n")[0][:200] or "Question from Slack",
        description=text,
        reporter=user_id,
        priority="Medium",
        created_at=datetime.now(tz=UTC),
        labels=["slack"],
        raw_payload={"slack_text": text, "user_id": user_id},
    )

    cfg = config_mod.get_config()
    reply = await support_review.review_ticket(
        ticket=ticket,
        envelope=_envelope_for_slack(channel=channel, user_id=user_id),
        agent_for=cfg.agent_for,
        document_searcher=cfg.build_document_searcher(),
        ticket_searcher=cfg.build_ticket_searcher(),
        status_update_client=status,
    )

    blocks = slack_blocks.support_summary_blocks(
        ticket_key=ticket.key,
        ticket_summary=ticket.summary,
        suggested_response=reply.suggested_response,
        confidence_label=reply.confidence.label.value if reply.confidence else None,
        category=reply.category,
    )
    await status.replace_with_result(
        text=f"Response suggestion ready for: {ticket.summary}",
        blocks=blocks,
    )

    logs.log_event(
        slack_constants.EVENT_SUPPORT_COMPLETE,
        params={"ticket_key": ticket.key, "channel": channel},
    )


async def _handle_request(
    text: str,
    *,
    client: Any,
    channel: str,
    thread_ts: str,
    user_id: str,
) -> None:
    clean_text = _strip_mention(text)

    if not clean_text:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=(
                "Hi! Describe an incident or support question and I'll investigate.\n\n"
                "*SRE examples:* _'payment-service pods are OOMKilling'_ or "
                "_'high latency on checkout API'_\n"
                "*Support examples:* _'How do I reset my API key?'_ or "
                "_'user cannot log in after domain migration'_"
            ),
        )
        return

    classified_intent = await _classify_intent(clean_text)
    is_sre = classified_intent == intent_router.Intent.SRE

    logs.log_event(
        slack_constants.EVENT_REQUEST_RECEIVED,
        params={
            "user_id": user_id,
            "channel": channel,
            "is_sre": is_sre,
            "intent": classified_intent.value,
        },
    )

    if is_sre:
        await _run_sre(clean_text, client=client, channel=channel, thread_ts=thread_ts)
    else:
        await _run_support(
            clean_text, client=client, channel=channel, thread_ts=thread_ts, user_id=user_id
        )


# ---------------------------------------------------------------------------
# Bolt event handlers
# ---------------------------------------------------------------------------


@app.event("app_mention")
async def handle_app_mention(
    event: dict[str, Any],
    client: Any,
    ack: AsyncAck,
) -> None:
    """Handle @Sentinel mentions in any channel."""
    await ack()
    mention = slack_parsers.parse_mention_event(event)
    try:
        await _handle_request(
            mention.text,
            client=client,
            channel=mention.channel,
            thread_ts=mention.thread_ts,
            user_id=mention.user_id,
        )
    except* Exception as eg:
        for exc in eg.exceptions:
            logs.log_exception(exc)
        logs.log_event(
            slack_constants.EVENT_REQUEST_ERROR,
            params={"channel": mention.channel, "error_count": len(eg.exceptions)},
        )
        await client.chat_postMessage(
            channel=mention.channel,
            thread_ts=mention.thread_ts,
            text=":x: Something went wrong while processing your request. Please try again.",
        )


@app.event("message")
async def handle_direct_message(
    event: dict[str, Any],
    client: Any,
    ack: AsyncAck,
) -> None:
    """Handle direct messages to the bot."""
    await ack()
    if event.get("bot_id") or event.get("subtype") or event.get("channel_type") != "im":
        return
    message = slack_parsers.parse_message_event(event)
    try:
        await _handle_request(
            message.text,
            client=client,
            channel=message.channel,
            thread_ts=message.thread_ts,
            user_id=message.user_id,
        )
    except* Exception as eg:
        for exc in eg.exceptions:
            logs.log_exception(exc)
        logs.log_event(
            slack_constants.EVENT_REQUEST_ERROR,
            params={"channel": message.channel, "error_count": len(eg.exceptions)},
        )
        await client.chat_postMessage(
            channel=message.channel,
            thread_ts=message.thread_ts,
            text=":x: Something went wrong while processing your request. Please try again.",
        )
