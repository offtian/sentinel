from __future__ import annotations

import os

import litellm

from sentinel import bootstrap_otel
from sentinel.settings import settings
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
    litellm.drop_params = True  # Ignore unsupported params per provider
    litellm.request_timeout = 300  # type: ignore[attr-defined]  # Local models can be slow

    ollama_url = settings.ollama_base_url.rstrip("/")
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
    (RFC §2.4, ADR 0007). The virtual key is never logged.

    Both ``litellm_base_url`` and ``litellm_virtual_key`` must be set; partial
    config falls into ``litellm.proxy.disabled`` so bootstrap matches the
    helper's fail-safe semantics. Bootstrap reads the fields directly rather
    than calling into ``domain.llm.litellm_proxy`` to respect the
    import-linter layering contract (bootstrap may not import domain).
    """
    if settings.litellm_base_url is None or settings.litellm_virtual_key is None:
        logs.log_event("litellm.proxy.disabled", params={"fallback": "in_process_sdk"})
        return

    logs.log_event("litellm.proxy.enabled", params={"host": str(settings.litellm_base_url)})


def initialise() -> None:
    global _initialised  # noqa: PLW0603
    if _initialised:
        return
    _configure_llm_env()
    logs.configure_logging()
    _log_litellm_proxy_state()

    bootstrap_otel.init_traces()
    _initialised = True
