"""Tests for the LLM replay-capture wrapper :class:`CapturingModel` (F4.7 slice B)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import messages as pydantic_ai_messages
from pydantic_ai import usage as pydantic_ai_usage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models import test as pydantic_ai_test_model

from sentinel.plugins.models import capturing as capturing_mod
from sentinel.plugins.models import recorded as recorded_model_mod
from sentinel.plugins.toolsets import _runtime as runtime_mod
from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


def _build_request_messages() -> list[pydantic_ai_messages.ModelMessage]:
    """Return a one-message message list suitable for CapturingModel.request."""
    return [
        pydantic_ai_messages.ModelRequest(
            parts=[pydantic_ai_messages.UserPromptPart(content="hello")],
        )
    ]


def _build_bundle(builder: bundle_mod.ReplayBundleBuilder) -> bundle_mod.ReplayBundle:
    """Materialise a bundle with placeholder envelope/alert for assertions."""
    return builder.build(
        envelope=factories.make_envelope(),
        alert_payload={},
        runbook_id=None,
        runbook_version_sha=None,
        final_outputs={},
    )


class _StubModel(pydantic_ai_test_model.TestModel):
    """
    Subclass of :class:`TestModel` that returns a deterministic response with
    a configurable :class:`RequestUsage`.

    :class:`TestModel` is the canonical pydantic-ai stub so we inherit it to
    keep the abstract :meth:`Model.request` contract honoured without
    re-implementing all of its prompt-handling logic.
    """

    def __init__(
        self, *, response_text: str, usage: pydantic_ai_usage.RequestUsage | None
    ) -> None:
        super().__init__(model_name="stub-model")
        self._response_text = response_text
        self._stub_usage = usage

    async def request(
        self,
        messages: list[pydantic_ai_messages.ModelMessage],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
    ) -> pydantic_ai_messages.ModelResponse:
        del messages, model_settings, model_request_parameters  # unused
        return pydantic_ai_messages.ModelResponse(
            parts=[pydantic_ai_messages.TextPart(content=self._response_text)],
            model_name="stub-model",
            usage=self._stub_usage
            if self._stub_usage is not None
            else pydantic_ai_usage.RequestUsage(),
        )


def _build_capturing(
    *, response_text: str = "ok", usage: pydantic_ai_usage.RequestUsage | None = None
) -> capturing_mod.CapturingModel:
    return capturing_mod.CapturingModel(
        wrapped=_StubModel(response_text=response_text, usage=usage),
        agent_name="alert_classifier",
    )


class TestCapturingModelRequest:
    async def test_records_one_llm_entry_when_builder_is_bound(self) -> None:
        # Given a CapturingModel wrapping a stub Model and a bound replay builder
        capturing = _build_capturing(response_text="classified")
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When the wrapper's request is invoked
            response = await capturing.request(
                _build_request_messages(),
                None,
                ModelRequestParameters(),
            )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # Then the underlying response was returned and one LLM entry was captured
        assert response.parts[0].content == "classified"
        bundle = _build_bundle(builder)
        assert len(bundle.llm_io) == 1
        recorded = bundle.llm_io[0]
        assert recorded.agent_name == "alert_classifier"
        assert recorded.model_id == "stub-model"
        assert "messages" in recorded.inputs
        assert isinstance(recorded.inputs["messages"], list)
        assert recorded.inputs["messages"][0]["parts"][0]["content"] == "hello"
        assert recorded.outputs["parts"][0]["content"] == "classified"

    async def test_is_noop_capture_when_no_builder_bound(self) -> None:
        # Given a CapturingModel with no replay builder bound
        capturing = _build_capturing(response_text="noop")
        assert runtime_mod.current_replay_builder() is None

        # When request is invoked
        response = await capturing.request(
            _build_request_messages(),
            None,
            ModelRequestParameters(),
        )

        # Then the response was returned and no capture happened
        assert response.parts[0].content == "noop"
        assert runtime_mod.current_replay_builder() is None

    async def test_propagates_token_usage_when_present(self) -> None:
        # Given a stub model that reports non-zero token usage
        usage = pydantic_ai_usage.RequestUsage(input_tokens=42, output_tokens=7)
        capturing = _build_capturing(response_text="ok", usage=usage)
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When the wrapper's request is invoked
            await capturing.request(
                _build_request_messages(),
                None,
                ModelRequestParameters(),
            )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # Then the captured token_usage carries the input/output counts
        bundle = _build_bundle(builder)
        recorded = bundle.llm_io[0]
        assert recorded.token_usage is not None
        assert recorded.token_usage["input_tokens"] == 42
        assert recorded.token_usage["output_tokens"] == 7

    async def test_token_usage_is_none_when_response_has_no_usage_values(self) -> None:
        # Given a stub model whose RequestUsage has no non-zero values
        capturing = _build_capturing(response_text="ok", usage=pydantic_ai_usage.RequestUsage())
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When the wrapper's request is invoked
            await capturing.request(
                _build_request_messages(),
                None,
                ModelRequestParameters(),
            )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # Then token_usage is None on the captured entry
        bundle = _build_bundle(builder)
        recorded = bundle.llm_io[0]
        assert recorded.token_usage is None


class TestCapturedOutputsRoundTripIntoModelResponse:
    async def test_outputs_can_be_reconstructed_via_recorded_model_helper(self) -> None:
        # Given a captured response from CapturingModel
        capturing = _build_capturing(response_text="round-trippable")
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            await capturing.request(
                _build_request_messages(),
                None,
                ModelRequestParameters(),
            )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # When the captured outputs dict is fed to the slice-C reconstruction helper
        bundle = _build_bundle(builder)
        recorded = bundle.llm_io[0]
        rebuilt = recorded_model_mod.reconstruct_model_response(recorded.outputs)

        # Then the reconstructed ModelResponse carries the original text part
        assert rebuilt.parts[0].content == "round-trippable"
