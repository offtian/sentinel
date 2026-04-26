from __future__ import annotations

import os

import litellm

from sentinel import bootstrap_otel, settings
from sentinel.utils import logs


_initialised = False


def _configure_llm_env() -> None:
    """
    Configure LiteLLM SDK and provider-specific environment variables.

    Routing in this codebase splits on model prefix
    (see :func:`sentinel.config._normalise_model_name`):

    - ``ollama/<model>`` → PydanticAI's OpenAI-compat client repointed at
      Ollama's OpenAI-compatible endpoint (``<OLLAMA_BASE_URL>/v1``). We
      override ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` to that endpoint
      so the OpenAI provider talks to the local Ollama daemon. (Ollama
      ignores the API key but ``openai-python`` rejects an empty one.)
    - ``openai/<model>`` → PydanticAI's ``litellm:`` prefix, which builds
      an OpenAI HTTP client targeted at ``LITELLM_BASE_URL`` (a real
      LiteLLM proxy). Local-dev with ``LITELLM_BASE_URL=""`` will only
      work for OpenAI when ``OPENAI_API_KEY`` is set on the host.

    LiteLLM SDK settings (``drop_params``, ``request_timeout``) are kept
    so any direct ``litellm.completion(...)`` calls still behave well.
    """
    cfg = settings.get_settings()

    litellm.drop_params = True  # Ignore unsupported params per provider
    litellm.request_timeout = 300  # type: ignore[attr-defined]  # Local models can be slow

    ollama_url = cfg.ollama_base_url.rstrip("/")
    if not ollama_url.endswith("/v1"):
        ollama_url = f"{ollama_url}/v1"

    os.environ.setdefault("OLLAMA_BASE_URL", ollama_url)
    # Repoint PydanticAI's OpenAI-compat client at Ollama for ollama/* models.
    # ``setdefault`` so an explicit OPENAI_BASE_URL pointing elsewhere wins.
    os.environ.setdefault("OPENAI_BASE_URL", ollama_url)
    os.environ.setdefault("OPENAI_API_KEY", "ollama")


def _log_litellm_proxy_state() -> None:
    """
    Emit a structured-log event recording whether the LiteLLM proxy is wired
    (RFC §2.4, ADR 0007).

    - ``litellm_base_url`` unset -> WARNING ``litellm_proxy_disabled`` with a
      ``fallback=in_process_sdk`` marker. This is the local-dev path.
    - ``litellm_base_url`` set -> INFO ``litellm_proxy_enabled`` with the
      proxy host. The virtual key is NEVER logged.
    """
    cfg = settings.get_settings()
    if cfg.litellm_base_url is None:
        logs.get_logger().warning(
            "litellm_proxy_disabled",
            fallback="in_process_sdk",
        )
        return

    logs.log_event(
        "litellm_proxy_enabled",
        params={"host": str(cfg.litellm_base_url)},
    )


def initialise() -> None:
    global _initialised  # noqa: PLW0603
    if _initialised:
        return
    _configure_llm_env()
    logs.configure_logging()
    _log_litellm_proxy_state()

    bootstrap_otel.init_traces()
    _initialised = True
