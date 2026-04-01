"""
Streamlit chat interface for testing Sentinel pipelines locally.

Provides the same conversational experience as the Slack bot — type a
message, get an SRE investigation or support review — without needing
Slack credentials, ECR, or a deployed hostname.

Run with::

    make run-chat
    # or
    uv run streamlit run src/sentinel/interfaces/chat/app.py
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import streamlit as st

from sentinel import bootstrap
from sentinel.domain.search import factory as search_factory
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.chat.status_update import StreamlitStatusUpdateClient
from sentinel.interfaces.graphs import common, sre_investigation, support_review
from sentinel.interfaces.graphs.agents import intent_router, utils
from sentinel.settings import get_settings


# ---------------------------------------------------------------------------
# Async helper — Streamlit runs in a sync context
# ---------------------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from Streamlit's synchronous context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Ollama model discovery
# ---------------------------------------------------------------------------

_MODEL_ROLES = (
    ("intent_router", "Intent Router"),
    ("classifier", "Alert Classifier"),
    ("analyser", "Root Cause Analyser"),
    ("reviewer", "Ticket Reviewer"),
    ("drafter", "Response Drafter"),
)

_SETTINGS_KEY_FOR_ROLE: dict[str, str] = {
    "intent_router": "intent_router_llm",
    "classifier": "alert_classifier_llm",
    "analyser": "root_cause_llm",
    "reviewer": "ticket_reviewer_llm",
    "drafter": "response_drafter_llm",
}


@st.cache_data(ttl=30)
def _fetch_ollama_models() -> tuple[str, ...]:
    """
    Fetch available model names from the local Ollama instance.

    Returns a tuple of model names (e.g. ``("qwen3:8b", "qwen3-coder:30b")``).
    Results are cached for 30 seconds to avoid hammering the API on every rerun.
    """
    gateway_url = get_settings().ai_gateway_url.rstrip("/")
    try:
        resp = httpx.get(f"{gateway_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return tuple(sorted(m["name"] for m in models))
    except Exception:
        return ()


def _selected_model(role: str) -> str:
    """Return the ``ollama/<model>`` string for a given role from session state."""
    return f"ollama/{st.session_state[f'model_{role}']}"


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------


async def _classify_intent(
    text: str,
    *,
    trace_collector: common.TraceCollector | None = None,
) -> intent_router.IntentClassification:
    """Route the user message to SRE or Support via the intent router agent."""
    result = await intent_router.agent.run(
        user_prompt=text,
        model=utils.get_model_with_gateway(_selected_model("intent_router")),
        deps=intent_router.Dependencies(message=text),
    )
    if trace_collector:
        trace_collector.record(
            agent_name="Intent Router",
            messages=result.all_messages(),
        )
    return result.output


# ---------------------------------------------------------------------------
# Pipeline runners
# ---------------------------------------------------------------------------


async def _run_sre(
    text: str,
    *,
    on_status: Callable[[str], None],
    trace_collector: common.TraceCollector | None = None,
) -> common.InvestigationReply:
    status_client = StreamlitStatusUpdateClient(on_status=on_status)

    first_line = text.split("\n")[0][:200]
    now = datetime.now(tz=UTC)
    alert = sre_entities.Alert(
        id=f"chat-{now.timestamp():.0f}",
        source="manual",
        title=first_line or "Alert from chat",
        description=text,
        severity=sre_entities.AlertSeverity.MEDIUM,
        service="unknown",
        triggered_at=now,
        raw_payload={"chat_text": text},
    )

    return await sre_investigation.investigate_alert(
        alert=alert,
        holmes=holmes_adapter.HolmesAdapter(enabled=get_settings().holmesgpt_enabled),
        status_update_client=status_client,
        classifier_model=_selected_model("classifier"),
        analyser_model=_selected_model("analyser"),
        post_to_slack=False,
        trace_collector=trace_collector,
    )


async def _run_support(
    text: str,
    *,
    on_status: Callable[[str], None],
    trace_collector: common.TraceCollector | None = None,
) -> common.SupportReply:
    status_client = StreamlitStatusUpdateClient(on_status=on_status)

    now = datetime.now(tz=UTC)
    ticket = support_entities.Ticket(
        id=f"chat-{now.timestamp():.0f}",
        key=f"CHAT-{now.strftime('%H%M%S')}",
        summary=text.split("\n")[0][:200] or "Question from chat",
        description=text,
        reporter="local-user",
        priority="Medium",
        created_at=now,
        labels=["chat"],
        raw_payload={"chat_text": text},
    )

    return await support_review.review_ticket(
        ticket=ticket,
        document_searcher=search_factory.build_document_searcher(),
        ticket_searcher=search_factory.build_ticket_searcher(),
        status_update_client=status_client,
        reviewer_model=_selected_model("reviewer"),
        drafter_model=_selected_model("drafter"),
        trace_collector=trace_collector,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_investigation(reply: common.InvestigationReply) -> str:
    confidence_label = reply.confidence.label.value if reply.confidence else "Unknown"
    confidence_icon = {"High": "🟢", "Medium": "🟡"}.get(confidence_label, "🔴")

    parts = [
        f"### Investigation: {reply.alert_id}",
        f"**Confidence:** {confidence_icon} {confidence_label}",
        "",
        f"**Root Cause:**\n{reply.root_cause or '_Unable to determine._'}",
    ]
    if reply.remediation:
        parts.append(f"\n**Remediation:**\n{reply.remediation}")
    if reply.findings_summary:
        parts.append(f"\n**Findings:**\n{reply.findings_summary}")
    return "\n".join(parts)


def _render_request_part(part: Any) -> None:
    """Render a single request-side message part."""
    if part.part_kind == "system-prompt":
        st.markdown("**System Prompt**")
        st.code(part.content, language="text")
    elif part.part_kind == "user-prompt":
        content = part.content if isinstance(part.content, str) else str(part.content)
        st.markdown("**User Prompt**")
        st.code(content, language="text")
    elif part.part_kind == "tool-return":
        st.markdown(f"**Tool Return** (`{part.tool_name}`)")
        st.code(str(part.content), language="json")
    elif part.part_kind == "retry-prompt":
        st.markdown("**Retry Prompt**")
        st.warning(str(part.content))


def _render_response_part(part: Any) -> None:
    """Render a single response-side message part."""
    if part.part_kind == "thinking":
        st.markdown("**Thinking**")
        st.code(part.content, language="text")
    elif part.part_kind == "text":
        st.markdown("**Response**")
        st.code(part.content, language="text")
    elif part.part_kind == "tool-call":
        args = part.args if isinstance(part.args, str) else json.dumps(part.args, indent=2)
        st.markdown(f"**Tool Call** (`{part.tool_name}`)")
        st.code(args, language="json")


def _render_trace(traces: list[common.AgentTrace]) -> None:
    """Render agent traces as expandable sections in the Streamlit chat."""
    for trace in traces:
        with st.expander(f"Agent: {trace.agent_name}", expanded=False):
            for message in trace.messages:
                if message.kind == "request":
                    for part in message.parts:
                        _render_request_part(part)
                elif message.kind == "response":
                    model_label = message.model_name or "unknown"
                    usage = message.usage
                    token_info = (
                        f"{usage.input_tokens} in / {usage.output_tokens} out" if usage else ""
                    )
                    st.caption(f"Model: `{model_label}` | Tokens: {token_info}")
                    for response_part in message.parts:
                        _render_response_part(response_part)


def _format_support(reply: common.SupportReply) -> str:
    confidence_label = reply.confidence.label.value if reply.confidence else "Unknown"
    confidence_icon = {"High": "🟢", "Medium": "🟡"}.get(confidence_label, "🔴")

    parts = [
        "### Response Suggestion",
        f"**Category:** {reply.category or 'Unknown'}  "
        f"**Confidence:** {confidence_icon} {confidence_label}",
        "",
        f"**Suggested Response:**\n{reply.suggested_response}",
    ]
    sources = reply.sources or []
    if sources:
        source_lines = "\n".join(
            f"- [{s['title']}]({s['url']})" if s.get("url") else f"- {s['title']}"
            for s in sources[:5]
        )
        parts.append(f"\n**Sources:**\n{source_lines}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Example scenarios — derived from PRD alert/ticket categories
# ---------------------------------------------------------------------------

_SRE_SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "label": "Pod CrashLoopBackOff",
        "prompt": (
            "ALERT: Pod CrashLoopBackOff in production\n\n"
            "Several pods in the payments-service deployment are stuck in "
            "CrashLoopBackOff. The OOMKilled reason appears in pod events. "
            "This started after the latest deployment rolled out at 14:32 UTC. "
            "Customer-facing payment processing is degraded with intermittent 503 errors."
        ),
    },
    {
        "label": "Database connection pool exhausted",
        "prompt": (
            "CRITICAL: PostgreSQL connection pool exhausted on orders-db\n\n"
            "Active connections have hit the max_connections limit (200). "
            "The orders-service and inventory-service are both logging "
            "'connection pool timeout' errors. Queries are queueing and p99 "
            "latency has spiked from 50ms to 12s. No recent schema migrations "
            "or config changes. Started gradually over the past 30 minutes."
        ),
    },
    {
        "label": "Elevated 5xx error rate",
        "prompt": (
            "WARNING: 5xx error rate above threshold on api-gateway\n\n"
            "The api-gateway service is returning HTTP 502 and 503 errors at "
            "a rate of 15% (threshold: 1%). Upstream health checks to the "
            "user-service are failing intermittently. The issue correlates "
            "with a traffic spike from a marketing campaign that launched "
            "at 09:00 UTC today."
        ),
    },
    {
        "label": "High memory usage on worker nodes",
        "prompt": (
            "ALERT: Node memory utilisation above 90% on worker-pool-2\n\n"
            "Three out of five nodes in worker-pool-2 are reporting memory "
            "utilisation above 92%. The cluster autoscaler has not triggered "
            "because CPU utilisation is normal. Multiple pods are approaching "
            "their memory limits and evictions have started. The ML batch "
            "pipeline cron jobs kicked off at their scheduled 02:00 UTC window."
        ),
    },
    {
        "label": "Certificate expiry imminent",
        "prompt": (
            "URGENT: TLS certificate expiring in 12 hours\n\n"
            "The wildcard certificate for *.prod.example.com expires in 12 "
            "hours. cert-manager renewal has been failing for the past 3 days "
            "with ACME challenge errors. DNS-01 challenge validation is "
            "returning SERVFAIL. If not renewed, all HTTPS traffic to "
            "production services will fail."
        ),
    },
)

_SUPPORT_SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "label": "SSO/SAML configuration",
        "prompt": (
            "We're trying to set up SAML SSO with Okta for our organisation "
            "but keep getting a 'SAML Response Signature Validation Failed' "
            "error when users try to log in. We've uploaded the IdP metadata "
            "XML and configured the ACS URL as shown in the docs. Our Okta "
            "admin says the SAML assertion looks correct on their side. "
            "Can you help us troubleshoot this?"
        ),
    },
    {
        "label": "API rate limiting",
        "prompt": (
            "Our integration is hitting rate limits on the /v2/events API "
            "endpoint. We're receiving HTTP 429 responses after about 100 "
            "requests per minute, but our contract says we should have a "
            "limit of 500 req/min. We need higher throughput for our event "
            "ingestion pipeline. What are our options for increasing the "
            "rate limit or optimising our request pattern?"
        ),
    },
    {
        "label": "Data export request",
        "prompt": (
            "We need to export all audit log data for the period between "
            "January 1st and March 15th 2026 for our annual compliance "
            "review. The data should include user login events, permission "
            "changes, and API access logs. Is there a self-service way to "
            "do this, or do we need to file a formal data request? We need "
            "the export in CSV format."
        ),
    },
    {
        "label": "Webhook delivery failures",
        "prompt": (
            "Our webhook endpoint at https://hooks.example.com/sentinel "
            "stopped receiving events about 2 hours ago. The last successful "
            "delivery was at 10:45 UTC. Our endpoint is up and returning 200 "
            "on health checks. We're not seeing any requests hitting our load "
            "balancer from your IPs. We've checked our firewall rules and "
            "nothing has changed. Can you check the delivery logs?"
        ),
    },
    {
        "label": "New team onboarding",
        "prompt": (
            "We're onboarding a new engineering team of 15 people who will "
            "need access to the platform. They need: project-level read/write "
            "access to the 'mobile-app' and 'mobile-api' projects, ability to "
            "create and manage their own API keys, and access to the staging "
            "environment. What's the recommended way to set this up? Should "
            "we use team-level permissions or individual roles?"
        ),
    },
)


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------


def _render_sidebar() -> None:
    """Render the sidebar with example scenarios and configuration info."""
    with st.sidebar:
        st.header("Example Scenarios")
        st.caption("Click a scenario to populate the chat input.")

        st.subheader("SRE Investigation")
        for scenario in _SRE_SCENARIOS:
            if st.button(scenario["label"], key=f"sre-{scenario['label']}"):
                st.session_state["prefill"] = scenario["prompt"]
                st.rerun()

        st.subheader("Support Review")
        for scenario in _SUPPORT_SCENARIOS:
            if st.button(scenario["label"], key=f"support-{scenario['label']}"):
                st.session_state["prefill"] = scenario["prompt"]
                st.rerun()

        st.divider()
        st.header("Model Selection")

        settings = get_settings()
        available = _fetch_ollama_models()

        if not available:
            st.warning("Could not reach Ollama — using defaults from .env")

        for role, label in _MODEL_ROLES:
            settings_key = _SETTINGS_KEY_FOR_ROLE[role]
            default_value = getattr(settings, settings_key).removeprefix("ollama/")
            if available:
                default_idx = available.index(default_value) if default_value in available else 0
                st.selectbox(
                    label,
                    options=available,
                    index=default_idx,
                    key=f"model_{role}",
                )
            else:
                st.text_input(label, value=default_value, key=f"model_{role}")

        if st.button("Refresh models"):
            _fetch_ollama_models.clear()
            st.rerun()

        st.divider()
        st.header("Debug")
        st.toggle("Show agent traces", key="show_traces", value=False)

        st.divider()
        st.caption(f"Gateway: `{settings.ai_gateway_url}`")
        st.caption(f"Doc search: `{settings.document_searcher}`")
        st.caption(f"Observability: `{settings.observability_backend or 'auto'}`")

        st.divider()
        st.markdown(
            "**Tip:** Set `DOCUMENT_SEARCHER=mock` in `.env` for fully "
            "offline testing with canned search results."
        )


def _render() -> None:
    st.set_page_config(page_title="Sentinel Chat", page_icon="🛡️", layout="wide")

    st.title("🛡️ Sentinel Chat")
    st.caption(
        "Local testing interface — same pipelines as the Slack bot.  "
        "An LLM intent router classifies your message and routes to the "
        "SRE investigation or support review pipeline."
    )

    _render_sidebar()

    # Chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("traces") and st.session_state.get("show_traces", False):
                _render_trace(msg["traces"])

    # User input — check for prefilled scenario or manual entry
    user_input = st.chat_input("Describe an incident or ask a support question...")
    prefill = st.session_state.pop("prefill", None)
    if prefill:
        user_input = prefill
    if not user_input:
        return

    # Display user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Run pipeline with live status
    with st.chat_message("assistant"):
        status_placeholder = st.empty()

        def _on_status(message: str) -> None:
            status_placeholder.info(f"⏳ {message}")

        status_placeholder.info("⏳ Classifying intent...")
        collector = common.TraceCollector()

        try:
            classification = _run_async(_classify_intent(user_input, trace_collector=collector))
            is_sre = classification.intent == intent_router.Intent.SRE
            route_label = "SRE Investigation" if is_sre else "Support Review"
            status_placeholder.info(f"⏳ Routed to **{route_label}** — {classification.rationale}")

            if is_sre:
                reply = _run_async(
                    _run_sre(user_input, on_status=_on_status, trace_collector=collector)
                )
                formatted = _format_investigation(reply)
            else:
                reply = _run_async(
                    _run_support(user_input, on_status=_on_status, trace_collector=collector)
                )
                formatted = _format_support(reply)

            status_placeholder.empty()
            st.markdown(formatted)
            st.session_state.messages.append(
                {"role": "assistant", "content": formatted, "traces": collector.traces}
            )

            if collector.traces and st.session_state.get("show_traces", False):
                _render_trace(collector.traces)

        except Exception as exc:
            status_placeholder.empty()
            error_msg = f"**Error:** {exc}"
            st.error(error_msg)
            st.session_state.messages.append({"role": "assistant", "content": error_msg})


bootstrap.initialise()
_render()
