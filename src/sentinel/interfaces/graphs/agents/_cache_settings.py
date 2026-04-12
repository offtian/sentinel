"""
Vendor-agnostic prompt cache settings for PydanticAI agents.

Detects the LLM provider from the model name and returns the
appropriate ``model_settings`` dict to enable system-prompt caching.
"""

from __future__ import annotations

from typing import Any


def _is_anthropic(model_name: str) -> bool:
    lower = model_name.lower()
    return lower.startswith(("anthropic/", "anthropic:")) or "claude-" in lower


def _is_openai(model_name: str) -> bool:
    lower = model_name.lower()
    return lower.startswith(("openai/", "openai:"))


def build_cache_settings(
    *,
    model_name: str,
    prompt_sha256: str,
) -> dict[str, Any] | None:
    """
    Return PydanticAI ``model_settings`` that enable system-prompt caching.

    :param model_name: Provider-prefixed model identifier
        (e.g. ``"openai:gpt-4.1"`` or ``"anthropic:claude-sonnet-4-6"``).
    :param prompt_sha256: SHA-256 digest of the static system prompt,
        used as a cache key for providers that support explicit keying.
    :returns: A settings dict suitable for ``agent.run(model_settings=...)``,
        or ``None`` when the provider has no caching mechanism.
    """
    if _is_anthropic(model_name):
        return {"anthropic_cache_instructions": True}
    if _is_openai(model_name):
        return {"openai_prompt_cache_key": prompt_sha256}
    return None
