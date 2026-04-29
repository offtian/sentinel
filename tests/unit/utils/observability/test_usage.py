"""
Unit tests for utils/observability/usage.py — PydanticAI Usage → UsageAttributes.
"""

from __future__ import annotations

import dataclasses
from unittest import mock

import pytest

from sentinel.utils.observability import semconv
from sentinel.utils.observability import usage as obs_usage


@dataclasses.dataclass(kw_only=True)
class _FakeUsage:
    """Minimal stand-in for pydantic_ai.usage.RunUsage."""

    input_tokens: int = 0
    output_tokens: int = 0


class TestExtractUsage:
    def test_token_counts_map_to_usage_attributes(self) -> None:
        # Given a PydanticAI usage object with known token counts
        fake_usage = _FakeUsage(input_tokens=200, output_tokens=80)

        # When extracting usage with litellm cost lookup suppressed
        with mock.patch(
            "sentinel.utils.observability.usage._cost_usd",
            return_value=0.0,
        ):
            result = obs_usage.extract_usage(fake_usage, model_name="openai/gpt-4.1")

        # Then token counts land on the result
        assert result.gen_ai_usage_input_tokens == 200
        assert result.gen_ai_usage_output_tokens == 80
        assert result.gen_ai_usage_total_tokens == 280

    def test_total_tokens_is_sum_of_input_and_output(self) -> None:
        # Given a usage with asymmetric token counts
        fake_usage = _FakeUsage(input_tokens=300, output_tokens=100)

        # When extracting usage
        with mock.patch(
            "sentinel.utils.observability.usage._cost_usd",
            return_value=0.0,
        ):
            result = obs_usage.extract_usage(fake_usage, model_name="openai/gpt-4.1-mini")

        # Then total equals input + output
        assert result.gen_ai_usage_total_tokens == 400

    def test_cost_usd_comes_from_litellm_lookup(self) -> None:
        # Given a usage object with known token counts
        fake_usage = _FakeUsage(input_tokens=100, output_tokens=50)

        # When cost lookup returns a known value
        with mock.patch(
            "sentinel.utils.observability.usage._cost_usd",
            return_value=0.0045,
        ) as patched_cost:
            result = obs_usage.extract_usage(fake_usage, model_name="openai/gpt-4.1")

        # Then sentinel_cost_usd reflects that value
        assert result.sentinel_cost_usd == pytest.approx(0.0045)
        patched_cost.assert_called_once_with(
            model_name="openai/gpt-4.1",
            input_tokens=100,
            output_tokens=50,
        )

    def test_cost_defaults_to_zero_on_litellm_error(self) -> None:
        # Given a usage where the model is unknown to litellm
        fake_usage = _FakeUsage(input_tokens=50, output_tokens=25)

        # When litellm raises (unknown model)
        with mock.patch(
            "sentinel.utils.observability.usage._cost_usd",
            side_effect=Exception("Unknown model"),
        ):
            result = obs_usage.extract_usage(fake_usage, model_name="unknown/model")

        # Then cost defaults to 0.0 without propagating the exception
        assert result.sentinel_cost_usd == 0.0

    def test_to_otel_dict_on_result_uses_dot_notation_keys(self) -> None:
        # Given extracted usage
        fake_usage = _FakeUsage(input_tokens=10, output_tokens=5)

        with mock.patch(
            "sentinel.utils.observability.usage._cost_usd",
            return_value=0.001,
        ):
            result = obs_usage.extract_usage(fake_usage, model_name="openai/gpt-4.1")

        # When calling to_otel_dict
        otel = result.to_otel_dict()

        # Then keys follow OTel dot notation
        assert semconv.GEN_AI_USAGE_INPUT_TOKENS in otel
        assert semconv.GEN_AI_USAGE_OUTPUT_TOKENS in otel
        assert semconv.GEN_AI_USAGE_TOTAL_TOKENS in otel
        assert "sentinel.cost_usd" in otel
