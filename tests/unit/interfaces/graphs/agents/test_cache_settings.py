"""Unit tests for prompt cache settings utilities."""

from __future__ import annotations

import pytest

from sentinel.interfaces.graphs.agents import _cache_settings


_DUMMY_SHA = "a" * 64


class TestIsAnthropic:
    """Tests for Anthropic model detection."""

    @pytest.mark.parametrize(
        "model_name",
        [
            "anthropic/claude-sonnet-4-6",
            "anthropic:claude-sonnet-4-6",
            "openai/claude-3-haiku",
            "litellm/claude-sonnet-4-6",
        ],
    )
    def test_detects_anthropic_models(self, model_name: str) -> None:
        """
        Given a model name containing a claude identifier,
        When _is_anthropic is called,
        Then it returns True.
        """
        assert _cache_settings._is_anthropic(model_name) is True

    @pytest.mark.parametrize(
        "model_name",
        [
            "openai/gpt-4.1",
            "openai:gpt-4.1-mini",
            "ollama/llama3",
            "test",
        ],
    )
    def test_rejects_non_anthropic_models(self, model_name: str) -> None:
        """
        Given a model name without a claude identifier,
        When _is_anthropic is called,
        Then it returns False.
        """
        assert _cache_settings._is_anthropic(model_name) is False


class TestIsOpenAI:
    """Tests for OpenAI model detection."""

    @pytest.mark.parametrize(
        "model_name",
        [
            "openai/gpt-4.1",
            "openai:gpt-4.1-mini",
            "openai/o3-mini",
        ],
    )
    def test_detects_openai_models(self, model_name: str) -> None:
        """
        Given a model name with openai prefix,
        When _is_openai is called,
        Then it returns True.
        """
        assert _cache_settings._is_openai(model_name) is True

    @pytest.mark.parametrize(
        "model_name",
        [
            "anthropic/claude-sonnet-4-6",
            "ollama/llama3",
            "test",
        ],
    )
    def test_rejects_non_openai_models(self, model_name: str) -> None:
        """
        Given a model name without openai prefix,
        When _is_openai is called,
        Then it returns False.
        """
        assert _cache_settings._is_openai(model_name) is False


class TestBuildCacheSettings:
    """Tests for the main build_cache_settings function."""

    def test_returns_anthropic_cache_instructions_for_claude(self) -> None:
        """
        Given an Anthropic model name,
        When build_cache_settings is called,
        Then it returns anthropic_cache_instructions=True.
        """
        result = _cache_settings.build_cache_settings(
            model_name="anthropic:claude-sonnet-4-6",
            prompt_sha256=_DUMMY_SHA,
        )

        assert result == {"anthropic_cache_instructions": True}

    def test_returns_openai_cache_key_for_gpt(self) -> None:
        """
        Given an OpenAI model name,
        When build_cache_settings is called,
        Then it returns openai_prompt_cache_key set to the sha256.
        """
        result = _cache_settings.build_cache_settings(
            model_name="openai:gpt-4.1",
            prompt_sha256=_DUMMY_SHA,
        )

        assert result == {"openai_prompt_cache_key": _DUMMY_SHA}

    def test_returns_none_for_unknown_provider(self) -> None:
        """
        Given an unrecognised model name,
        When build_cache_settings is called,
        Then it returns None.
        """
        result = _cache_settings.build_cache_settings(
            model_name="ollama/llama3",
            prompt_sha256=_DUMMY_SHA,
        )

        assert result is None

    def test_returns_none_for_test_model(self) -> None:
        """
        Given the placeholder "test" model,
        When build_cache_settings is called,
        Then it returns None.
        """
        result = _cache_settings.build_cache_settings(
            model_name="test",
            prompt_sha256=_DUMMY_SHA,
        )

        assert result is None

    def test_anthropic_takes_priority_over_openai_prefix(self) -> None:
        """
        Given a model routed through openai/ but containing 'claude-',
        When build_cache_settings is called,
        Then Anthropic settings are returned (claude detection wins).
        """
        result = _cache_settings.build_cache_settings(
            model_name="openai/claude-3-haiku",
            prompt_sha256=_DUMMY_SHA,
        )

        assert result == {"anthropic_cache_instructions": True}
