from __future__ import annotations

import os

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
    name the selected provider expects.
    """
    gateway_url = os.environ.get("AI_GATEWAY_URL")
    if gateway_url:
        os.environ.setdefault("OPENAI_BASE_URL", gateway_url)
        os.environ.setdefault("OLLAMA_BASE_URL", gateway_url)
    os.environ.setdefault("OPENAI_API_KEY", "sentinel-not-needed")


def initialise() -> None:
    global _initialised  # noqa: PLW0603
    if _initialised:
        return
    _configure_llm_env()
    logs.configure_logging()
    _initialised = True
