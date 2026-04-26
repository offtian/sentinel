from __future__ import annotations

from typing import Self
from unittest import mock

import httpx
import pytest

from sentinel.utils import llm_warmup
from sentinel.utils import logs as logs_mod


def _settings_with_models(
    *,
    base_url: str = "http://ollama:11434",
    classifier: str = "ollama/qwen3:8b",
    root_cause: str = "ollama/qwen3:8b",
    ticket_reviewer: str = "openai/gpt-4.1-mini",
    response_drafter: str = "ollama/llama3:70b",
    k8s_investigator: str = "openai/gpt-4.1",
) -> mock.MagicMock:
    # The helper only ever reads attributes off ``Settings``; a MagicMock
    # with the relevant fields is the smallest stand-in that avoids
    # touching the real env-driven Settings singleton.
    cfg = mock.MagicMock()
    cfg.ollama_base_url = base_url
    cfg.alert_classifier_llm = classifier
    cfg.root_cause_llm = root_cause
    cfg.ticket_reviewer_llm = ticket_reviewer
    cfg.response_drafter_llm = response_drafter
    cfg.k8s_investigator_llm = k8s_investigator
    return cfg


class TestOllamaModelsFromSettings:
    def test_returns_only_ollama_prefixed_models(self):
        # Given a settings mock with two ollama models and three non-ollama
        cfg = _settings_with_models()

        # When the helper extracts ollama models
        with mock.patch.object(llm_warmup, "settings", cfg):
            models = llm_warmup._ollama_models_from_settings()

        # Then only the ollama/* entries are returned, prefix-stripped,
        # in declaration order
        assert models == ("qwen3:8b", "llama3:70b")

    def test_deduplicates_repeated_models(self):
        # Given a settings mock where multiple agent slots share a model
        cfg = _settings_with_models(
            classifier="ollama/qwen3:8b",
            root_cause="ollama/qwen3:8b",
            response_drafter="ollama/qwen3:8b",
        )

        # When the helper extracts models
        with mock.patch.object(llm_warmup, "settings", cfg):
            models = llm_warmup._ollama_models_from_settings()

        # Then the duplicate is collapsed to a single entry
        assert models == ("qwen3:8b",)

    def test_returns_empty_when_no_ollama_models_configured(self):
        # Given a settings mock with only OpenAI models
        cfg = _settings_with_models(
            classifier="openai/gpt-4.1-mini",
            root_cause="openai/gpt-4.1",
            response_drafter="openai/gpt-4.1",
        )

        # When the helper extracts ollama models
        with mock.patch.object(llm_warmup, "settings", cfg):
            models = llm_warmup._ollama_models_from_settings()

        # Then the result is empty
        assert models == ()


class TestWarmOllamaModels:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("settings_overrides", "expected_reason"),
        [
            ({"base_url": ""}, "ollama_base_url unset"),
            (
                {
                    "classifier": "openai/gpt-4.1-mini",
                    "root_cause": "openai/gpt-4.1",
                    "response_drafter": "openai/gpt-4.1",
                },
                "no ollama models configured",
            ),
        ],
        ids=["base_url_missing", "no_ollama_models"],
    )
    async def test_skips_warmup_with_reason(
        self, settings_overrides: dict[str, str], expected_reason: str
    ):
        # Given a settings mock that should short-circuit warmup
        cfg = _settings_with_models(**settings_overrides)

        # When warmup is called under patched HTTP and log sinks
        with (
            mock.patch.object(llm_warmup, "settings", cfg),
            mock.patch.object(httpx, "AsyncClient") as patched_client,
            mock.patch.object(logs_mod, "log_event") as patched_log,
        ):
            await llm_warmup.warm_ollama_models()

        # Then no HTTP client is constructed and the skip reason is logged
        patched_client.assert_not_called()
        skip_event = next(
            call for call in patched_log.call_args_list if call.args[0] == "llm.warmup.disabled"
        )
        assert skip_event.kwargs["params"]["reason"] == expected_reason

    @pytest.mark.asyncio
    async def test_strips_v1_suffix_added_by_bootstrap(self):
        # Given an ollama_base_url that already carries the OpenAI-compat
        # ``/v1`` suffix appended by ``bootstrap._configure_llm_env``
        cfg = _settings_with_models(base_url="http://ollama:11434/v1")

        # And a stub AsyncClient capturing every POST URL
        captured_urls: list[str] = []

        class _StubClient:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_exc: object) -> None:
                return None

            async def post(self, url: str, **_kwargs: object) -> mock.MagicMock:
                captured_urls.append(url)
                response = mock.MagicMock()
                response.raise_for_status = mock.MagicMock()
                return response

        # When warmup runs
        with (
            mock.patch.object(llm_warmup, "settings", cfg),
            mock.patch.object(httpx, "AsyncClient", _StubClient),
        ):
            await llm_warmup.warm_ollama_models()

        # Then the request targets the bare ollama root, not the /v1 path
        assert all(url == "http://ollama:11434/api/generate" for url in captured_urls)

    @pytest.mark.asyncio
    async def test_swallows_per_model_failure_and_continues(self):
        # Given two configured ollama models, one of which fails
        cfg = _settings_with_models(
            classifier="ollama/qwen3:8b",
            root_cause="ollama/llama3:70b",
        )

        class _StubClient:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_exc: object) -> None:
                return None

            async def post(self, url: str, **kwargs: object) -> mock.MagicMock:
                # The first model raises; the second succeeds. The helper
                # is expected to log the failure and complete normally.
                payload = kwargs["json"]
                if payload["model"] == "qwen3:8b":
                    raise httpx.ConnectError("daemon down")
                response = mock.MagicMock()
                response.raise_for_status = mock.MagicMock()
                return response

        # When warmup runs under patched logging
        with (
            mock.patch.object(llm_warmup, "settings", cfg),
            mock.patch.object(httpx, "AsyncClient", _StubClient),
            mock.patch.object(logs_mod, "log_exception") as patched_log_exc,
            mock.patch.object(logs_mod, "log_event") as patched_log_event,
        ):
            await llm_warmup.warm_ollama_models()

        # Then the failure is logged once and warmup still completes
        assert patched_log_exc.call_count == 1
        skip_params = patched_log_exc.call_args.kwargs["params"]
        assert skip_params["event"] == "llm.warmup.skipped"
        assert skip_params["model"] == "qwen3:8b"
        complete_event = next(
            call
            for call in patched_log_event.call_args_list
            if call.args[0] == "llm.warmup.complete"
        )
        assert complete_event.kwargs["params"]["models"] == ["qwen3:8b", "llama3:70b"]
