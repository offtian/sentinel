"""
Typed Pydantic wrappers for OTel span attributes.

Each model is ``frozen=True`` (boundary type per python.md), accepts
Python-safe underscore field names, and exposes ``.to_otel_dict()`` which
emits the dot-separated OTel key names that Langfuse and the RFC §13.2
mandatory-attrs contract expect.

Consumed by both the Pydantic Graph node helpers (legacy chart pipeline)
and the LangGraph workflow nodes (SRE + support pipelines).
"""

from __future__ import annotations

from opentelemetry.util import types as otel_types
from pydantic import BaseModel, ConfigDict

from sentinel.utils.observability import semconv


class NodeSpanAttributes(BaseModel):
    """
    Span attributes for a pipeline node (RFC §13.2 mandatory + node-local +
    Langfuse session/user grouping).

    Covers the six envelope-derived mandatory attrs, ``team_profile``,
    the ``pipeline``/``node`` labels, and the Langfuse-namespaced session
    and user IDs that route spans into Langfuse's Sessions and Users tabs.
    """

    model_config = ConfigDict(frozen=True)

    # RFC §13.2 mandatory — six envelope-derived
    request_id: str
    tenant_id: str
    cluster_id: str
    region: str
    pii_class: str
    received_at: str
    # Node-local
    pipeline: str
    node: str
    team_profile: str
    # Langfuse identity
    langfuse_session_id: str
    langfuse_user_id: str
    langfuse_observation_type: str = "chain"

    def to_otel_dict(self) -> dict[str, otel_types.AttributeValue]:
        """Return a flat dict of OTel attribute key→value pairs."""
        return {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "cluster_id": self.cluster_id,
            "region": self.region,
            "pii_class": self.pii_class,
            "received_at": self.received_at,
            "pipeline": self.pipeline,
            "node": self.node,
            "team_profile": self.team_profile,
            "langfuse.session.id": self.langfuse_session_id,
            "langfuse.user.id": self.langfuse_user_id,
            "langfuse.observation.type": self.langfuse_observation_type,
        }


class AgentSpanAttributes(BaseModel):
    """
    Span attributes for a PydanticAI agent invocation.

    Emits OTel GenAI semconv keys (``gen_ai.*``) alongside the Sentinel-named
    mandatory attrs (``prompt_version_sha``, ``model_id``) and Langfuse prompt
    registry attrs (``langfuse.prompt.*``) that were set by the old
    ``set_agent_span_attributes`` helper — preserving backward compatibility
    while adding new semconv visibility.
    """

    model_config = ConfigDict(frozen=True)

    gen_ai_system: str = "pydantic-ai"
    gen_ai_request_model: str = ""
    gen_ai_operation_name: str = "chat"
    # RFC §13.2 mandatory
    prompt_version_sha: str
    model_id: str = ""
    team_profile: str = ""
    agent_name: str = ""

    def to_otel_dict(self) -> dict[str, otel_types.AttributeValue]:
        """Return a flat dict of OTel attribute key→value pairs."""
        attrs: dict[str, otel_types.AttributeValue] = {
            semconv.GEN_AI_SYSTEM: self.gen_ai_system,
            semconv.GEN_AI_OPERATION_NAME: self.gen_ai_operation_name,
            "prompt_version_sha": self.prompt_version_sha,
            "langfuse.prompt.version": self.prompt_version_sha,
        }
        if self.gen_ai_request_model:
            attrs[semconv.GEN_AI_REQUEST_MODEL] = self.gen_ai_request_model
        if self.model_id:
            attrs["model_id"] = self.model_id
        if self.agent_name:
            attrs["langfuse.prompt.name"] = self.agent_name
        if self.team_profile:
            attrs["team_profile"] = self.team_profile
        return attrs


class ToolSpanAttributes(BaseModel):
    """
    Span attributes for a tool invocation inside a PydanticAI agent run.

    Includes the OTel GenAI tool semconv keys and an optional
    ``runbook_grant_id`` set by the F7 RunbookScopedToolset boundary.
    """

    model_config = ConfigDict(frozen=True)

    gen_ai_tool_name: str
    gen_ai_tool_call_id: str = ""
    runbook_grant_id: str | None = None

    def to_otel_dict(self) -> dict[str, otel_types.AttributeValue]:
        """Return a flat dict of OTel attribute key→value pairs."""
        attrs: dict[str, otel_types.AttributeValue] = {
            semconv.GEN_AI_TOOL_NAME: self.gen_ai_tool_name,
        }
        if self.gen_ai_tool_call_id:
            attrs[semconv.GEN_AI_TOOL_CALL_ID] = self.gen_ai_tool_call_id
        if self.runbook_grant_id is not None:
            attrs["runbook_grant_id"] = self.runbook_grant_id
        return attrs


class UsageAttributes(BaseModel):
    """
    Token-count and cost attributes from a PydanticAI agent run.

    Emits OTel GenAI usage semconv keys that Langfuse uses to populate
    Generation cost dashboards, plus a Sentinel-owned ``sentinel.cost_usd``
    derived from LiteLLM's pricing table.
    """

    model_config = ConfigDict(frozen=True)

    gen_ai_usage_input_tokens: int
    gen_ai_usage_output_tokens: int
    gen_ai_usage_total_tokens: int
    sentinel_cost_usd: float

    def to_otel_dict(self) -> dict[str, otel_types.AttributeValue]:
        """Return a flat dict of OTel attribute key→value pairs."""
        return {
            semconv.GEN_AI_USAGE_INPUT_TOKENS: self.gen_ai_usage_input_tokens,
            semconv.GEN_AI_USAGE_OUTPUT_TOKENS: self.gen_ai_usage_output_tokens,
            semconv.GEN_AI_USAGE_TOTAL_TOKENS: self.gen_ai_usage_total_tokens,
            "sentinel.cost_usd": self.sentinel_cost_usd,
        }
