"""
LiteLLM proxy plumbing helper (RFC §2.4).

Resolves whether the firm-shared LiteLLM proxy is configured and,
when it is, returns the kwargs every PydanticAI agent factory feeds
into ``LiteLLMProvider`` so the resulting ``OpenAIChatModel`` points
at the proxy URL with the operator's virtual key.

Both ``litellm_base_url`` and ``litellm_virtual_key`` must be set for
the helper to report configured. A partial config (one set, the other
unset) is treated as unconfigured and emits a structured-log warning
so the misconfiguration surfaces in startup logs rather than silently
sending unauthenticated traffic to a half-wired proxy.
"""

from __future__ import annotations

from typing import Any

from sentinel import config as config_module
from sentinel.utils import logs


_PARTIAL_CONFIG_WARNING_EVENT = "litellm_proxy_partial_config"


def get_proxy_kwargs() -> dict[str, Any] | None:
    """
    Return ``LiteLLMProvider`` kwargs when the proxy is configured, else ``None``.

    When both ``litellm_base_url`` and ``litellm_virtual_key`` are set on
    ``Settings``, returns ``{"api_base": ..., "api_key": ...}`` ready to
    pass straight into pydantic-ai's ``LiteLLMProvider``. When neither
    is set, returns ``None`` so the caller falls back to the in-process
    LiteLLM SDK path (``just run-api`` works locally without a proxy).

    Partial configs (one of the two fields set) emit the
    ``litellm_proxy_partial_config`` structured event and are treated
    as unconfigured — fail safe rather than silently constructing a
    half-wired proxy URL.

    Single source of truth: callers who only need the boolean
    "configured?" answer pattern-match on ``is None`` instead of using
    a separate predicate, so settings are read exactly once per call.
    """
    settings = config_module.get_config().settings
    base_url = settings.litellm_base_url
    virtual_key = settings.litellm_virtual_key

    if base_url is None and virtual_key is None:
        return None

    if base_url is None or virtual_key is None:
        logs.log_event(
            _PARTIAL_CONFIG_WARNING_EVENT,
            params={
                "base_url_set": base_url is not None,
                "virtual_key_set": virtual_key is not None,
            },
        )
        return None

    return {
        "api_base": str(base_url),
        "api_key": virtual_key.get_secret_value(),
    }
