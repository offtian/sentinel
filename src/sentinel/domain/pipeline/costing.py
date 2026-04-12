"""
Cost estimation helpers for LLM model calls.
"""

from __future__ import annotations

import litellm

from sentinel.utils import logs


def estimate_cost_usd(*, model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """
    Return the estimated cost in USD for a model call.

    Normalises PydanticAI model IDs (e.g. ``litellm:openai:gpt-4.1``) to the
    format LiteLLM expects (e.g. ``openai/gpt-4.1``).  Returns ``None`` when
    the model is unknown or the cost cannot be determined — this function must
    never crash the hot path.

    :param model_id: The model identifier, optionally prefixed with ``litellm:``.
    :param input_tokens: Number of prompt tokens consumed.
    :param output_tokens: Number of completion tokens generated.
    :returns: Estimated cost in USD, or ``None`` if cost cannot be determined.
    """
    normalised = _normalise_model_id(model_id)
    try:
        cost: float = litellm.completion_cost(
            model=normalised,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
        return cost
    except Exception as exc:
        logs.log_exception(
            exc,
            params={
                "model_id": model_id,
                "normalised_model_id": normalised,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
        return None


def _normalise_model_id(model_id: str) -> str:
    """
    Return a LiteLLM-compatible model identifier.

    Strips the ``litellm:`` prefix injected by PydanticAI, then replaces the
    first remaining ``:`` with ``/`` so ``openai:gpt-4.1`` becomes
    ``openai/gpt-4.1``.
    """
    without_prefix = model_id.removeprefix("litellm:")
    return without_prefix.replace(":", "/", 1)
