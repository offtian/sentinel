"""
PydanticAI Usage → UsageAttributes extraction.

``extract_usage`` reads token counts from a ``pydantic_ai.usage.RunUsage``
(or any object exposing ``input_tokens`` / ``output_tokens``) and looks up
the estimated cost via LiteLLM's pricing table. Cost failures are swallowed
so a missing pricing entry never breaks a pipeline run.
"""

from __future__ import annotations

from typing import Any

import litellm

from sentinel.utils import logs
from sentinel.utils.observability import spans as obs_spans


def _cost_usd(*, model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost from LiteLLM's pricing table."""
    prompt_cost, completion_cost = litellm.cost_per_token(
        model=model_name,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
    )
    return prompt_cost + completion_cost


def extract_usage(usage: Any, *, model_name: str) -> obs_spans.UsageAttributes:
    """
    Return a :class:`UsageAttributes` built from a PydanticAI ``RunUsage``.

    Reads ``input_tokens`` and ``output_tokens`` from ``usage``. Derives
    ``total_tokens`` as their sum. Estimates cost via LiteLLM; on any failure
    (unknown model, network issue, zero-token edge case) ``sentinel_cost_usd``
    is set to ``0.0`` and a structured warning is emitted.

    :param usage: A ``pydantic_ai.usage.RunUsage`` (or duck-typed equivalent).
    :param model_name: LiteLLM model string (e.g. ``"openai/gpt-4.1"``).
    """
    input_tokens: int = getattr(usage, "input_tokens", 0) or 0
    output_tokens: int = getattr(usage, "output_tokens", 0) or 0
    total_tokens = input_tokens + output_tokens

    try:
        cost = _cost_usd(
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"context": "otel.usage.cost_lookup_failed", "model_name": model_name},
        )
        cost = 0.0

    return obs_spans.UsageAttributes(
        gen_ai_usage_input_tokens=input_tokens,
        gen_ai_usage_output_tokens=output_tokens,
        gen_ai_usage_total_tokens=total_tokens,
        sentinel_cost_usd=cost,
    )
