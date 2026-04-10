from __future__ import annotations

import os

from sentinel import bootstrap_otel, settings
from sentinel.utils import logs


_initialised = False


def _configure_llm_env() -> None:
    """
    Bridge ``AI_GATEWAY_URL`` to the provider-specific environment variables.

    pydantic-ai selects a provider based on the model prefix (``openai:``,
    ``ollama:``, etc.) and each provider reads its own env vars:

    - **OpenAI** — ``OPENAI_BASE_URL``, ``OPENAI_API_KEY``
    - **Ollama** — ``OLLAMA_BASE_URL``

    This function ensures the gateway URL is available under whichever
    name the selected provider expects.  We read from :class:`Settings`
    so that values defined in ``.env`` are picked up even when the
    corresponding shell variable is not exported.
    """
    cfg = settings.get_settings()
    gateway_url = cfg.ai_gateway_url

    ollama_url = gateway_url.rstrip("/")
    if not ollama_url.endswith("/v1"):
        ollama_url = f"{ollama_url}/v1"

    os.environ.setdefault("OPENAI_BASE_URL", gateway_url)
    os.environ.setdefault("OLLAMA_BASE_URL", ollama_url)
    os.environ.setdefault("OPENAI_API_KEY", "sentinel-not-needed")


def initialise() -> None:
    global _initialised  # noqa: PLW0603
    if _initialised:
        return
    _configure_llm_env()
    logs.configure_logging()

    bootstrap_otel.init_traces()
    _initialised = True
