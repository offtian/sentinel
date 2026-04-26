"""
Pre-load Ollama models on worker startup so the first request does not
pay the cold-start tax of memory-mapping the model.
"""

from __future__ import annotations

import asyncio

import httpx

from sentinel.settings import settings
from sentinel.utils import logs


_OLLAMA_MODEL_PREFIX = "ollama/"
# 10 minutes balances "next pipeline run shares the loaded model" against
# "idle dev boxes release GPU memory promptly".
_WARMUP_KEEP_ALIVE = "10m"
_WARMUP_TIMEOUT_SECONDS = 60.0


def _ollama_models_from_settings() -> tuple[str, ...]:
    candidates = (
        settings.alert_classifier_llm,
        settings.root_cause_llm,
        settings.ticket_reviewer_llm,
        settings.response_drafter_llm,
        settings.k8s_investigator_llm,
    )
    seen: list[str] = []
    for raw in candidates:
        if not raw or not raw.startswith(_OLLAMA_MODEL_PREFIX):
            continue
        model = raw.removeprefix(_OLLAMA_MODEL_PREFIX)
        if model and model not in seen:
            seen.append(model)
    return tuple(seen)


async def _warm_one(client: httpx.AsyncClient, *, base_url: str, model: str) -> None:
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": _WARMUP_KEEP_ALIVE,
    }
    try:
        response = await client.post(url, json=payload, timeout=_WARMUP_TIMEOUT_SECONDS)
        response.raise_for_status()
        logs.log_event(
            "llm.warmup.loaded",
            params={"model": model, "base_url": base_url},
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={
                "event": "llm.warmup.skipped",
                "model": model,
                "base_url": base_url,
            },
        )


async def warm_ollama_models() -> None:
    """
    Best-effort POST ``/api/generate`` against every configured Ollama
    model so subsequent pipeline runs skip the model-load latency.
    """
    base_url = (settings.ollama_base_url or "").rstrip("/")
    if not base_url:
        logs.log_event("llm.warmup.disabled", params={"reason": "ollama_base_url unset"})
        return

    models = _ollama_models_from_settings()
    if not models:
        logs.log_event("llm.warmup.disabled", params={"reason": "no ollama models configured"})
        return

    # Strip the OpenAI-compat suffix bootstrap may have appended; Ollama's
    # native API lives at the bare host root.
    base_url = base_url.removesuffix("/v1")

    logs.log_event(
        "llm.warmup.start",
        params={"base_url": base_url, "models": list(models)},
    )

    async with httpx.AsyncClient() as client:
        await asyncio.gather(
            *(_warm_one(client, base_url=base_url, model=model) for model in models)
        )

    logs.log_event("llm.warmup.complete", params={"models": list(models)})
