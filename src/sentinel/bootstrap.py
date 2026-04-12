from __future__ import annotations

import os

import litellm

from sentinel import bootstrap_otel, settings
from sentinel.utils import logs


_initialised = False


def _configure_llm_env() -> None:
    """
    Configure LiteLLM SDK and provider-specific environment variables.

    LiteLLM runs in-process (SDK mode) — no external proxy.  PydanticAI
    delegates to LiteLLM via the ``litellm:`` model prefix, and LiteLLM
    routes to the correct provider based on the model name
    (e.g. ``openai/gpt-4.1-mini`` → OpenAI, ``ollama/qwen3:8b`` → Ollama).

    LiteLLM SDK settings replace the former ``litellm_config.yaml`` proxy
    configuration.  Provider-specific env vars:

    - **OpenAI** — ``OPENAI_API_KEY``
    - **Ollama** — ``OLLAMA_BASE_URL``
    """
    cfg = settings.get_settings()

    # LiteLLM SDK settings (equivalent to litellm_settings in proxy config)
    litellm.drop_params = True  # Ignore unsupported params per provider
    litellm.request_timeout = 300  # type: ignore[attr-defined]  # Local models can be slow

    ollama_url = cfg.ollama_base_url.rstrip("/")
    if not ollama_url.endswith("/v1"):
        ollama_url = f"{ollama_url}/v1"

    os.environ.setdefault("OLLAMA_BASE_URL", ollama_url)


def initialise() -> None:
    global _initialised  # noqa: PLW0603
    if _initialised:
        return
    _configure_llm_env()
    logs.configure_logging()

    bootstrap_otel.init_traces()
    _initialised = True
