from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.evaluation import costing


class TestEstimateCostUsd:
    def test_returns_cost_for_known_model(self) -> None:
        # Given litellm.completion_cost returns a known cost
        with mock.patch("litellm.completion_cost", return_value=0.0042) as patched:
            # When estimate_cost_usd is called with a known model
            result = costing.estimate_cost_usd(
                model_id="openai/gpt-4.1",
                input_tokens=100,
                output_tokens=50,
            )

        # Then the cost is returned and completion_cost was called correctly
        assert result == pytest.approx(0.0042)
        patched.assert_called_once_with(
            model="openai/gpt-4.1",
            prompt_tokens=100,
            completion_tokens=50,
        )

    def test_returns_none_for_unknown_model(self) -> None:
        # Given litellm.completion_cost raises for an unknown model
        with mock.patch("litellm.completion_cost", side_effect=Exception("Unknown model")):
            # When estimate_cost_usd is called with an unrecognised model
            result = costing.estimate_cost_usd(
                model_id="unknown/mystery-model",
                input_tokens=10,
                output_tokens=5,
            )

        # Then None is returned without raising
        assert result is None

    def test_normalises_pydantic_ai_model_id(self) -> None:
        # Given litellm.completion_cost is patched and a PydanticAI-style model ID
        with mock.patch("litellm.completion_cost", return_value=0.001) as patched:
            # When estimate_cost_usd is called with the litellm: prefixed model ID
            costing.estimate_cost_usd(
                model_id="litellm:openai:gpt-4.1",
                input_tokens=200,
                output_tokens=100,
            )

        # Then completion_cost receives the normalised openai/gpt-4.1 form
        patched.assert_called_once_with(
            model="openai/gpt-4.1",
            prompt_tokens=200,
            completion_tokens=100,
        )


class TestNormaliseModelId:
    def test_strips_litellm_prefix_and_replaces_colon(self) -> None:
        # Given a PydanticAI-style model ID with litellm: prefix
        # When _normalise_model_id is called
        result = costing._normalise_model_id("litellm:openai:gpt-4.1")

        # Then the prefix is stripped and the first colon replaced with a slash
        assert result == "openai/gpt-4.1"

    def test_replaces_first_colon_without_litellm_prefix(self) -> None:
        # Given a model ID without the litellm: prefix but with provider:model format
        # When _normalise_model_id is called
        result = costing._normalise_model_id("anthropic:claude-3-5-sonnet")

        # Then only the first colon is replaced with a slash
        assert result == "anthropic/claude-3-5-sonnet"

    def test_leaves_slash_separated_model_id_unchanged(self) -> None:
        # Given a model ID already in provider/model format
        # When _normalise_model_id is called
        result = costing._normalise_model_id("openai/gpt-4.1")

        # Then the ID is returned unchanged
        assert result == "openai/gpt-4.1"
