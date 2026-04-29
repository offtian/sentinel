"""
OTel GenAI semantic convention attribute name constants.

Mirrors ``opentelemetry.semconv._incubating.attributes.gen_ai_attributes``
symbols under Sentinel-owned names so import sites are insulated from the
still-incubating namespace's churn.
"""

from __future__ import annotations


GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
# Not an official semconv key; Sentinel extension for total token visibility.
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
