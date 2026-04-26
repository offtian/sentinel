"""Tests for the RecordedModel replay primitive (F4.7)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic_ai import messages as pydantic_ai_messages
from pydantic_ai.models import ModelRequestParameters

from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.plugins.models import recorded as recorded_model_mod
from sentinel.utils import replay_bundle as bundle_mod


_FIXED_AT = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def _make_response(
    *, text: str, model_name: str = "stub-model"
) -> pydantic_ai_messages.ModelResponse:
    return pydantic_ai_messages.ModelResponse(
        parts=[pydantic_ai_messages.TextPart(content=text)],
        model_name=model_name,
        timestamp=_FIXED_AT,
    )


def _make_llm_entry(
    *,
    response: pydantic_ai_messages.ModelResponse,
    agent_name: str = "stub-agent",
    model_id: str = "stub-model",
) -> bundle_mod.LLMIOEntry:
    return bundle_mod.LLMIOEntry(
        agent_name=agent_name,
        model_id=model_id,
        inputs={},
        outputs=recorded_model_mod.serialise_model_response(response),
        token_usage=None,
        at=_FIXED_AT,
    )


def _empty_request_messages() -> list[pydantic_ai_messages.ModelMessage]:
    return [
        pydantic_ai_messages.ModelRequest(
            parts=[pydantic_ai_messages.UserPromptPart(content="hi")],
        )
    ]


class TestRecordedModelOrder:
    @pytest.mark.asyncio
    async def test_returns_recorded_responses_in_order(self) -> None:
        # Given a RecordedModel seeded with two recorded LLM entries
        first_response = _make_response(text="first")
        second_response = _make_response(text="second")
        entries = (
            _make_llm_entry(response=first_response),
            _make_llm_entry(response=second_response),
        )
        model = recorded_model_mod.RecordedModel(entries)

        # When request is invoked twice
        first_actual = await model.request(
            _empty_request_messages(),
            None,
            ModelRequestParameters(),
        )
        second_actual = await model.request(
            _empty_request_messages(),
            None,
            ModelRequestParameters(),
        )

        # Then each call returns the recorded response in slot order
        assert first_actual.text == "first"
        assert second_actual.text == "second"


class TestRecordedModelExhaustion:
    @pytest.mark.asyncio
    async def test_raises_when_queue_is_empty(self) -> None:
        # Given an empty RecordedModel
        model = recorded_model_mod.RecordedModel(())

        # When request is invoked
        # Then RecordedReplayMismatchError is raised with kind="llm", reason="exhausted"
        with pytest.raises(pipeline_errors.RecordedReplayMismatchError) as exc_info:
            await model.request(
                _empty_request_messages(),
                None,
                ModelRequestParameters(),
            )
        assert exc_info.value.kind == "llm"
        assert exc_info.value.reason == "exhausted"


class TestModelResponseRoundTrip:
    def test_round_trip_preserves_text_part(self) -> None:
        # Given a ModelResponse with a single TextPart
        original = _make_response(text="round-trip")

        # When the response is serialised then reconstructed
        serialised = recorded_model_mod.serialise_model_response(original)
        reconstructed = recorded_model_mod.reconstruct_model_response(serialised)

        # Then the reconstructed response carries the same text
        assert reconstructed.text == "round-trip"
        assert reconstructed.model_name == original.model_name

    def test_round_trip_preserves_tool_call_part(self) -> None:
        # Given a ModelResponse mixing a TextPart and a ToolCallPart
        original = pydantic_ai_messages.ModelResponse(
            parts=[
                pydantic_ai_messages.TextPart(content="thinking"),
                pydantic_ai_messages.ToolCallPart(
                    tool_name="kubectl_logs",
                    args={"namespace": "ns"},
                    tool_call_id="call-1",
                ),
            ],
            model_name="stub-model",
            timestamp=_FIXED_AT,
        )

        # When the response is serialised then reconstructed
        serialised = recorded_model_mod.serialise_model_response(original)
        reconstructed = recorded_model_mod.reconstruct_model_response(serialised)

        # Then the reconstructed response retains the tool call
        assert len(reconstructed.tool_calls) == 1
        assert reconstructed.tool_calls[0].tool_name == "kubectl_logs"
        assert reconstructed.tool_calls[0].args == {"namespace": "ns"}


class TestRecordedModelStreaming:
    def test_request_stream_is_unsupported(self) -> None:
        # Given a RecordedModel
        model = recorded_model_mod.RecordedModel(())

        # When request_stream is accessed it raises NotImplementedError on entry
        # Then NotImplementedError surfaces (replay doesn't support streaming)
        async def _enter() -> None:
            async with model.request_stream(
                _empty_request_messages(),
                None,
                ModelRequestParameters(),
            ):
                pass  # pragma: no cover — should never reach here

        with pytest.raises(NotImplementedError):
            asyncio.run(_enter())
