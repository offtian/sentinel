from __future__ import annotations

import os

from sentinel import bootstrap_otel, settings
from sentinel.utils import logs


_initialised = False


def _configure_llm_env() -> None:
    """
    Set provider-specific environment variables for LLM routing.

    PydanticAI routes directly to providers based on the model prefix
    (``openai:``, ``ollama:``, etc.).  Each provider reads its own env vars:

    - **OpenAI** — ``OPENAI_BASE_URL`` (default ``https://api.openai.com/v1``),
      ``OPENAI_API_KEY``
    - **Ollama** — ``OLLAMA_BASE_URL``

    We read from :class:`Settings` so that values defined in ``.env`` are
    picked up even when the corresponding shell variable is not exported.
    """
    cfg = settings.get_settings()

    ollama_url = cfg.ollama_base_url.rstrip("/")
    if not ollama_url.endswith("/v1"):
        ollama_url = f"{ollama_url}/v1"

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
