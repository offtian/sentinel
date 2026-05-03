"""
T42 — Span attribute contract test for the SRE workflow typed observability layer.

Tests the attribute models directly by constructing each ``*Attributes`` class
with known values and asserting ``to_otel_dict()`` produces the mandatory keys
required by RFC §13.2 and the OTel GenAI semconv used by Langfuse.

This is a unit-style test in an integration test file — it verifies the
attribute contract without needing a running OTel pipeline or backend.

Mandatory keys asserted:
- ``gen_ai.system`` — from ``AgentSpanAttributes``
- ``gen_ai.operation.name`` — from ``AgentSpanAttributes``
- ``langfuse.session.id`` — from ``NodeSpanAttributes``
- ``sentinel.team_profile`` / ``team_profile`` — from ``NodeSpanAttributes`` and
  ``AgentSpanAttributes``

RFC §13.2 envelope attrs asserted on ``NodeSpanAttributes``:
- ``request_id``, ``tenant_id``, ``cluster_id``, ``region``, ``pii_class``, ``received_at``

Rationale for not capturing live OTel spans: ``ProxyTracerProvider`` does not
expose ``add_span_processor`` in all SDK configurations; live span capture is
tightly coupled to the bootstrap order and the Logfire integration. Testing the
attribute models directly is more reliable and tests exactly what we care about:
the shape of the dict passed to ``span.set_attributes(...)``.
"""

from __future__ import annotations

from sentinel.utils.observability import spans as spans_mod


class TestNodeSpanAttributesContract:
    """Verify ``NodeSpanAttributes.to_otel_dict()`` produces all mandatory keys."""

    def test_rfc_mandatory_envelope_attrs_present(self) -> None:
        """
        All six RFC §13.2 envelope-derived mandatory attributes are present in
        the ``to_otel_dict()`` output.
        """
        # Given a fully-populated NodeSpanAttributes instance
        attrs = spans_mod.NodeSpanAttributes(
            request_id="req-001",
            tenant_id="acme-corp",
            cluster_id="prod-eu-west-1",
            region="eu-west-1",
            pii_class="internal",
            received_at="2026-04-30T12:00:00Z",
            pipeline="sre_investigation",
            node="classify_alert",
            team_profile="sre",
            langfuse_session_id="req-001",
            langfuse_user_id="acme-corp",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then all RFC §13.2 mandatory envelope attrs are present
        assert "request_id" in otel_dict
        assert otel_dict["request_id"] == "req-001"
        assert "tenant_id" in otel_dict
        assert otel_dict["tenant_id"] == "acme-corp"
        assert "cluster_id" in otel_dict
        assert otel_dict["cluster_id"] == "prod-eu-west-1"
        assert "region" in otel_dict
        assert otel_dict["region"] == "eu-west-1"
        assert "pii_class" in otel_dict
        assert otel_dict["pii_class"] == "internal"
        assert "received_at" in otel_dict
        assert otel_dict["received_at"] == "2026-04-30T12:00:00Z"

    def test_langfuse_session_id_present(self) -> None:
        """
        ``langfuse.session.id`` is present and equals the langfuse_session_id field.

        This key routes all node spans for a single investigation under one
        Langfuse Session for trace grouping.
        """
        # Given a NodeSpanAttributes with a known session id
        attrs = spans_mod.NodeSpanAttributes(
            request_id="req-session-test",
            tenant_id="acme",
            cluster_id="dev-cluster",
            region="us-east-1",
            pii_class="public",
            received_at="2026-04-30T00:00:00Z",
            pipeline="sre_investigation",
            node="determine_confidence",
            team_profile="sre",
            langfuse_session_id="req-session-test",
            langfuse_user_id="acme",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then langfuse.session.id is present with the expected value
        assert "langfuse.session.id" in otel_dict
        assert otel_dict["langfuse.session.id"] == "req-session-test"

    def test_team_profile_present(self) -> None:
        """
        ``team_profile`` (Sentinel mandatory attr) is present in the dict.
        """
        # Given a NodeSpanAttributes with team_profile="devops"
        attrs = spans_mod.NodeSpanAttributes(
            request_id="req-tp-test",
            tenant_id="acme",
            cluster_id="dev-cluster",
            region="us-east-1",
            pii_class="public",
            received_at="2026-04-30T00:00:00Z",
            pipeline="sre_investigation",
            node="publish_findings",
            team_profile="devops",
            langfuse_session_id="req-tp-test",
            langfuse_user_id="acme",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then team_profile is present
        assert "team_profile" in otel_dict
        assert otel_dict["team_profile"] == "devops"


class TestAgentSpanAttributesContract:
    """Verify ``AgentSpanAttributes.to_otel_dict()`` produces GenAI semconv keys."""

    def test_gen_ai_system_and_operation_present(self) -> None:
        """
        ``gen_ai.system`` and ``gen_ai.operation.name`` are always present.

        These keys are required for Langfuse Generation views to show the
        model breakdown and cost dashboards.
        """
        # Given an AgentSpanAttributes with the alert_classifier context
        attrs = spans_mod.AgentSpanAttributes(
            prompt_version_sha="deadbeef01234567",
            gen_ai_system="pydantic-ai",
            gen_ai_operation_name="chat",
            gen_ai_request_model="openai/gpt-4.1-mini",
            model_id="openai/gpt-4.1-mini",
            team_profile="sre",
            agent_name="alert_classifier",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then the mandatory GenAI semconv keys are present
        assert "gen_ai.system" in otel_dict
        assert otel_dict["gen_ai.system"] == "pydantic-ai"
        assert "gen_ai.operation.name" in otel_dict
        assert otel_dict["gen_ai.operation.name"] == "chat"

    def test_gen_ai_request_model_included_when_set(self) -> None:
        """
        ``gen_ai.request.model`` is present when ``gen_ai_request_model`` is non-empty.
        """
        # Given an AgentSpanAttributes with a model set
        attrs = spans_mod.AgentSpanAttributes(
            prompt_version_sha="abc123",
            gen_ai_request_model="openai/gpt-4.1",
            model_id="gpt-4.1",
            team_profile="sre",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then gen_ai.request.model is included
        assert "gen_ai.request.model" in otel_dict
        assert otel_dict["gen_ai.request.model"] == "openai/gpt-4.1"

    def test_team_profile_included_when_set(self) -> None:
        """
        ``team_profile`` is present in the agent span dict when non-empty.
        """
        # Given an AgentSpanAttributes with team_profile set
        attrs = spans_mod.AgentSpanAttributes(
            prompt_version_sha="sha256abc",
            team_profile="sre",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then team_profile is present
        assert "team_profile" in otel_dict
        assert otel_dict["team_profile"] == "sre"


class TestToolSpanAttributesContract:
    """Verify ``ToolSpanAttributes.to_otel_dict()`` produces the tool semconv keys."""

    def test_gen_ai_tool_name_is_mandatory(self) -> None:
        """
        ``gen_ai.tool.name`` is always present in the dict.
        """
        # Given a ToolSpanAttributes with a tool name
        attrs = spans_mod.ToolSpanAttributes(
            gen_ai_tool_name="get_pod_metrics",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then gen_ai.tool.name is present
        assert "gen_ai.tool.name" in otel_dict
        assert otel_dict["gen_ai.tool.name"] == "get_pod_metrics"

    def test_runbook_grant_id_included_when_set(self) -> None:
        """
        ``runbook_grant_id`` is included in the dict when set (F7 RunbookGrant).
        """
        # Given a ToolSpanAttributes with a runbook grant id
        attrs = spans_mod.ToolSpanAttributes(
            gen_ai_tool_name="k8s_get_pod_logs",
            runbook_grant_id="grant-abc-123",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then runbook_grant_id is included
        assert "runbook_grant_id" in otel_dict
        assert otel_dict["runbook_grant_id"] == "grant-abc-123"

    def test_runbook_grant_id_absent_when_none(self) -> None:
        """
        ``runbook_grant_id`` is absent from the dict when not set.
        """
        # Given a ToolSpanAttributes without runbook_grant_id
        attrs = spans_mod.ToolSpanAttributes(
            gen_ai_tool_name="datadog_query_logs",
        )

        # When to_otel_dict is called
        otel_dict = attrs.to_otel_dict()

        # Then runbook_grant_id is absent (not included as None)
        assert "runbook_grant_id" not in otel_dict
