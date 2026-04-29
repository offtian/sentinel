"""
Unit tests for utils/observability/spans.py — typed span-attribute models.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentinel.utils.observability import semconv
from sentinel.utils.observability import spans as obs_spans


class TestNodeSpanAttributes:
    def test_to_otel_dict_contains_all_envelope_mandatory_attrs(self) -> None:
        # Given a fully populated NodeSpanAttributes
        attrs = obs_spans.NodeSpanAttributes(
            request_id="req-abc",
            tenant_id="tenant-x",
            cluster_id="cluster-1",
            region="us-east-1",
            pii_class="internal",
            received_at="2026-04-29T10:00:00+00:00",
            pipeline="sre",
            node="classify_alert",
            team_profile="sre",
            langfuse_session_id="req-abc",
            langfuse_user_id="tenant-x",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then all RFC §13.2 envelope mandatory attrs are present
        assert result["request_id"] == "req-abc"
        assert result["tenant_id"] == "tenant-x"
        assert result["cluster_id"] == "cluster-1"
        assert result["region"] == "us-east-1"
        assert result["pii_class"] == "internal"
        assert result["received_at"] == "2026-04-29T10:00:00+00:00"
        assert result["team_profile"] == "sre"

    def test_to_otel_dict_contains_pipeline_and_node_attrs(self) -> None:
        # Given NodeSpanAttributes with pipeline and node
        attrs = obs_spans.NodeSpanAttributes(
            request_id="req-1",
            tenant_id="t",
            cluster_id="c",
            region="r",
            pii_class="public",
            received_at="2026-01-01T00:00:00+00:00",
            pipeline="sre",
            node="investigate",
            team_profile="sre",
            langfuse_session_id="req-1",
            langfuse_user_id="t",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then pipeline and node keys are present
        assert result["pipeline"] == "sre"
        assert result["node"] == "investigate"

    def test_to_otel_dict_contains_langfuse_session_attrs(self) -> None:
        # Given NodeSpanAttributes with Langfuse identity
        attrs = obs_spans.NodeSpanAttributes(
            request_id="req-xyz",
            tenant_id="acme",
            cluster_id="prod",
            region="eu-west-1",
            pii_class="confidential",
            received_at="2026-04-29T00:00:00+00:00",
            pipeline="support",
            node="classify_ticket",
            team_profile="devops",
            langfuse_session_id="req-xyz",
            langfuse_user_id="acme",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then langfuse.session.id and langfuse.user.id are present with dot-notation keys
        assert result["langfuse.session.id"] == "req-xyz"
        assert result["langfuse.user.id"] == "acme"
        assert result["langfuse.observation.type"] == "chain"

    def test_langfuse_observation_type_defaults_to_chain(self) -> None:
        # Given NodeSpanAttributes without explicit observation_type
        attrs = obs_spans.NodeSpanAttributes(
            request_id="r",
            tenant_id="t",
            cluster_id="c",
            region="region",
            pii_class="public",
            received_at="2026-01-01T00:00:00+00:00",
            pipeline="sre",
            node="n",
            team_profile="sre",
            langfuse_session_id="r",
            langfuse_user_id="t",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then the observation type defaults to "chain"
        assert result["langfuse.observation.type"] == "chain"

    def test_model_is_frozen(self) -> None:
        # Given a NodeSpanAttributes instance
        attrs = obs_spans.NodeSpanAttributes(
            request_id="r",
            tenant_id="t",
            cluster_id="c",
            region="r2",
            pii_class="public",
            received_at="2026-01-01T00:00:00+00:00",
            pipeline="sre",
            node="n",
            team_profile="sre",
            langfuse_session_id="r",
            langfuse_user_id="t",
        )

        # When attempting mutation
        # Then a ValidationError or TypeError is raised (Pydantic frozen model)
        with pytest.raises((ValidationError, TypeError, AttributeError)):
            attrs.request_id = "other"  # type: ignore[misc]


class TestAgentSpanAttributes:
    def test_to_otel_dict_emits_gen_ai_semconv_keys(self) -> None:
        # Given an AgentSpanAttributes for a named model
        attrs = obs_spans.AgentSpanAttributes(
            gen_ai_request_model="openai/gpt-4.1",
            prompt_version_sha="deadbeef",
            model_id="openai/gpt-4.1",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then gen_ai.* semconv keys are present
        assert result[semconv.GEN_AI_SYSTEM] == "pydantic-ai"
        assert result[semconv.GEN_AI_REQUEST_MODEL] == "openai/gpt-4.1"
        assert result[semconv.GEN_AI_OPERATION_NAME] == "chat"

    def test_to_otel_dict_preserves_sentinel_named_attrs(self) -> None:
        # Given an AgentSpanAttributes
        attrs = obs_spans.AgentSpanAttributes(
            gen_ai_request_model="anthropic/claude-3-5-sonnet",
            prompt_version_sha="abc123",
            model_id="anthropic/claude-3-5-sonnet",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then backward-compatible Sentinel attrs are preserved
        assert result["prompt_version_sha"] == "abc123"
        assert result["model_id"] == "anthropic/claude-3-5-sonnet"
        assert result["langfuse.prompt.version"] == "abc123"

    def test_to_otel_dict_includes_agent_name_as_langfuse_prompt_name(self) -> None:
        # Given an AgentSpanAttributes with agent_name set
        attrs = obs_spans.AgentSpanAttributes(
            gen_ai_request_model="openai/gpt-4.1",
            prompt_version_sha="sha1",
            model_id="openai/gpt-4.1",
            agent_name="alert_classifier",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then langfuse.prompt.name is present
        assert result["langfuse.prompt.name"] == "alert_classifier"

    def test_to_otel_dict_omits_langfuse_prompt_name_when_agent_name_empty(self) -> None:
        # Given an AgentSpanAttributes without agent_name
        attrs = obs_spans.AgentSpanAttributes(
            gen_ai_request_model="openai/gpt-4.1",
            prompt_version_sha="sha1",
            model_id="openai/gpt-4.1",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then langfuse.prompt.name is absent
        assert "langfuse.prompt.name" not in result

    def test_to_otel_dict_omits_model_id_when_empty(self) -> None:
        # Given an AgentSpanAttributes with no model (test/mock)
        attrs = obs_spans.AgentSpanAttributes(
            gen_ai_request_model="",
            prompt_version_sha="sha1",
            model_id="",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then model_id is absent (test models skip the attribute)
        assert "model_id" not in result
        assert semconv.GEN_AI_REQUEST_MODEL not in result

    def test_model_is_frozen(self) -> None:
        # Given an AgentSpanAttributes instance
        attrs = obs_spans.AgentSpanAttributes(
            gen_ai_request_model="m",
            prompt_version_sha="s",
            model_id="m",
        )

        # When attempting mutation
        # Then ValidationError is raised (frozen Pydantic v2 model)
        with pytest.raises((ValidationError, TypeError, AttributeError)):
            attrs.prompt_version_sha = "other"  # type: ignore[misc]


class TestToolSpanAttributes:
    def test_to_otel_dict_contains_tool_name(self) -> None:
        # Given a ToolSpanAttributes for a known tool
        attrs = obs_spans.ToolSpanAttributes(gen_ai_tool_name="query_metrics")

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then gen_ai.tool.name is present
        assert result[semconv.GEN_AI_TOOL_NAME] == "query_metrics"

    def test_to_otel_dict_omits_call_id_when_empty(self) -> None:
        # Given a ToolSpanAttributes without call_id
        attrs = obs_spans.ToolSpanAttributes(gen_ai_tool_name="search_docs")

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then gen_ai.tool.call.id is absent
        assert semconv.GEN_AI_TOOL_CALL_ID not in result

    def test_to_otel_dict_includes_call_id_when_set(self) -> None:
        # Given a ToolSpanAttributes with an explicit call ID
        attrs = obs_spans.ToolSpanAttributes(
            gen_ai_tool_name="query_logs",
            gen_ai_tool_call_id="call-001",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then gen_ai.tool.call.id is present
        assert result[semconv.GEN_AI_TOOL_CALL_ID] == "call-001"

    def test_to_otel_dict_includes_runbook_grant_id_when_present(self) -> None:
        # Given a ToolSpanAttributes with a runbook grant ID (F7)
        attrs = obs_spans.ToolSpanAttributes(
            gen_ai_tool_name="query_logs",
            runbook_grant_id="grant-abc",
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then runbook_grant_id is present
        assert result["runbook_grant_id"] == "grant-abc"

    def test_to_otel_dict_omits_runbook_grant_id_when_none(self) -> None:
        # Given a ToolSpanAttributes without a runbook grant
        attrs = obs_spans.ToolSpanAttributes(gen_ai_tool_name="search")

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then runbook_grant_id is absent
        assert "runbook_grant_id" not in result


class TestUsageAttributes:
    def test_to_otel_dict_contains_token_and_cost_keys(self) -> None:
        # Given a UsageAttributes with token counts and cost
        attrs = obs_spans.UsageAttributes(
            gen_ai_usage_input_tokens=100,
            gen_ai_usage_output_tokens=50,
            gen_ai_usage_total_tokens=150,
            sentinel_cost_usd=0.003,
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then all gen_ai.usage.* and sentinel.cost_usd keys are present
        assert result[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 100
        assert result[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 50
        assert result[semconv.GEN_AI_USAGE_TOTAL_TOKENS] == 150
        assert result["sentinel.cost_usd"] == pytest.approx(0.003)

    def test_to_otel_dict_keys_use_dot_notation(self) -> None:
        # Given a UsageAttributes
        attrs = obs_spans.UsageAttributes(
            gen_ai_usage_input_tokens=10,
            gen_ai_usage_output_tokens=5,
            gen_ai_usage_total_tokens=15,
            sentinel_cost_usd=0.0,
        )

        # When converting to OTel dict
        result = attrs.to_otel_dict()

        # Then keys use OTel dot-notation (not Python underscore form)
        assert "gen_ai_usage_input_tokens" not in result
        assert "gen_ai.usage.input_tokens" in result
