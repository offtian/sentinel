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


def is_proxy_configured() -> bool:
    """
    Return True when both LiteLLM proxy fields are set.

    Reads ``litellm_base_url`` and ``litellm_virtual_key`` off
    ``get_config().settings``. Both must be set for the proxy path to
    activate; partial configs fail safe back to unconfigured.
    """
    settings = config_module.get_config().settings
    return settings.litellm_base_url is not None and settings.litellm_virtual_key is not None


def get_proxy_kwargs() -> dict[str, Any]:
    """
    Return ``LiteLLMProvider`` kwargs when the proxy is configured.

    When configured, returns a dict with ``api_base`` and ``api_key``
    keys that pydantic-ai's ``LiteLLMProvider`` accepts directly. When
    unconfigured (either field unset), returns an empty dict so the
    caller falls back to the in-process LiteLLM SDK path.

    Partial configs (one of the two fields set) emit a structured-log
    warning and are treated as unconfigured.
    """
    settings = config_module.get_config().settings
    base_url = settings.litellm_base_url
    virtual_key = settings.litellm_virtual_key

    if base_url is None and virtual_key is None:
        return {}

    if base_url is None or virtual_key is None:
        # Operator forgot one of the two — fail safe back to in-process
        # SDK rather than silently constructing a half-wired proxy URL.
        # Emit a structured event so the misconfiguration surfaces in
        # startup logs.
        logs.log_event(
            _PARTIAL_CONFIG_WARNING_EVENT,
            params={
                "base_url_set": base_url is not None,
                "virtual_key_set": virtual_key is not None,
            },
        )
        return {}

    return {
        "api_base": str(base_url),
        "api_key": virtual_key.get_secret_value(),
    }
