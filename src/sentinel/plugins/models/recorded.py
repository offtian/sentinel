"""
Replay primitive: ``WrapperModel`` subclass that pops recorded LLM I/O in order.

Used by the F4.7 replay CLI to substitute a single ordered queue of
:class:`~sentinel.utils.replay_bundle.LLMIOEntry` for every PydanticAI
:class:`~pydantic_ai.models.Model` in a pipeline. The original capture
path records all LLM calls into one global timeline regardless of which
agent issued them, so a single :class:`RecordedModel` shared across
every agent's ``model`` slot reproduces the same global order.

Replay is strict on order: any drift raises
:class:`~sentinel.domain.pipeline.errors.RecordedReplayMismatchError`.
Streaming is not supported — ``request_stream`` raises
:class:`NotImplementedError` because the production agent paths under
replay are non-streaming and per-token reconstruction has no determinism
benefit.

The reconstruction round-trip uses PydanticAI's own
``ModelMessagesTypeAdapter`` so that every supported part type
(``TextPart``, ``ToolCallPart``, ``BuiltinToolCallPart``,
``ThinkingPart``, ...) re-validates losslessly without us hand-rolling
the discriminator dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pydantic
from pydantic_ai import messages as pydantic_ai_messages
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.utils import replay_bundle as bundle_mod


_RECORDED_MODEL_NAME = "recorded"

# A typed adapter pinned to ``ModelResponse`` (the messages adapter
# already shipped by PydanticAI uses ``list[ModelMessage]`` which is too
# wide for our single-response round-trip).
_MODEL_RESPONSE_ADAPTER: pydantic.TypeAdapter[pydantic_ai_messages.ModelResponse] = (
    pydantic.TypeAdapter(pydantic_ai_messages.ModelResponse)
)


def serialise_model_response(
    response: pydantic_ai_messages.ModelResponse,
) -> dict[str, Any]:
    """
    Return a JSON-safe dict for *response* using PydanticAI's own dump path.

    Uses ``mode="json"`` so datetimes / enums / bytes are coerced to their
    JSON representations and the resulting dict round-trips through
    :func:`reconstruct_model_response` without surprise type drift.
    """
    dumped: dict[str, Any] = _MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json")
    return dumped


def reconstruct_model_response(
    payload: dict[str, Any],
) -> pydantic_ai_messages.ModelResponse:
    """
    Rebuild a :class:`ModelResponse` from a previously-serialised *payload*.

    Symmetric inverse of :func:`serialise_model_response`. Validates via
    PydanticAI's adapter so structured parts (tool calls, builtin tool
    returns, ...) reconstruct as the same dataclass types as on the
    capture path.
    """
    return _MODEL_RESPONSE_ADAPTER.validate_python(payload)


class _NoOpModel(Model):
    """Inner model handed to :class:`WrapperModel` — never called."""

    @property
    def model_name(self) -> str:
        return _RECORDED_MODEL_NAME

    @property
    def system(self) -> str:
        return _RECORDED_MODEL_NAME

    async def request(
        self,
        messages: list[pydantic_ai_messages.ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> pydantic_ai_messages.ModelResponse:
        del messages, model_settings, model_request_parameters
        raise NotImplementedError(
            "_NoOpModel.request must never be invoked — RecordedModel overrides request entirely."
        )


class RecordedModel(WrapperModel):
    """
    WrapperModel that returns recorded LLM responses in invocation order.

    On every :meth:`request` the model pops the next
    :class:`~sentinel.utils.replay_bundle.LLMIOEntry` from its queue and
    rebuilds the recorded :class:`~pydantic_ai.messages.ModelResponse`
    from the entry's ``outputs`` (a JSON-safe dict produced by
    :func:`serialise_model_response` on the capture path). Exhaustion
    raises
    :class:`~sentinel.domain.pipeline.errors.RecordedReplayMismatchError`
    with ``kind="llm", reason="exhausted"``.

    Streaming is not supported — :meth:`request_stream` raises
    :class:`NotImplementedError`.
    """

    def __init__(self, entries: Sequence[bundle_mod.LLMIOEntry]) -> None:
        super().__init__(_NoOpModel())
        self._iter: Iterator[bundle_mod.LLMIOEntry] = iter(entries)

    @property
    def model_name(self) -> str:
        """Return a stable replay marker rather than the wrapped no-op name."""
        return _RECORDED_MODEL_NAME

    async def request(
        self,
        messages: list[pydantic_ai_messages.ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> pydantic_ai_messages.ModelResponse:
        """Pop the next recorded entry and return its reconstructed response."""
        del messages, model_settings, model_request_parameters
        try:
            entry = next(self._iter)
        except StopIteration as exc:
            raise pipeline_errors.RecordedReplayMismatchError(
                kind="llm",
                expected=None,
                actual=None,
                reason="exhausted",
            ) from exc
        return reconstruct_model_response(entry.outputs)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[pydantic_ai_messages.ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        """Streaming is unsupported under replay — always raises ``NotImplementedError``."""
        del messages, model_settings, model_request_parameters, run_context
        if False:  # pragma: no cover — required to make this a generator for type checking.
            yield  # type: ignore[unreachable]
        raise NotImplementedError(
            "RecordedModel does not support streaming — capture/replay is non-streaming."
        )
