"""
LLM replay-capture wrapper for PydanticAI ``Model`` instances (RFC §3.8 / F4.7).

:class:`CapturingModel` is a :class:`pydantic_ai.models.WrapperModel`
subclass that records every model invocation into the active replay
builder bound on the current asyncio context. It is the LLM-side
counterpart to
:class:`sentinel.plugins.toolsets._runtime.ReplayCapturingToolset` —
together they capture the complete I/O surface a pipeline run touches
so :mod:`sentinel.replay` can re-execute the run bit-for-bit later.

Production paths that don't bind a builder pay only one ``ContextVar.get()``
per LLM call: :func:`record_llm_call` is a fast no-op when no builder
is bound. Streaming runs are not supported by this slice — the
PydanticAI agents in this codebase always use the non-streaming
``agent.run()`` path.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai import messages as pydantic_ai_messages
from pydantic_ai import usage as pydantic_ai_usage
from pydantic_ai._run_context import RunContext
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models import wrapper as pydantic_ai_wrapper
from pydantic_ai.settings import ModelSettings

from sentinel.plugins.models import recorded as recorded_model_mod
from sentinel.plugins.toolsets import _runtime as runtime
from sentinel.utils import logs


logger = logs.get_logger()


def _serialise_messages(
    messages: list[pydantic_ai_messages.ModelMessage],
) -> list[dict[str, Any]]:
    """
    Return a JSON-safe representation of *messages* for capture.

    Uses PydanticAI's own ``ModelMessagesTypeAdapter`` with ``mode="json"``
    so that every supported part type round-trips losslessly through
    Postgres JSONB and back into the same dataclass shapes on replay.
    """
    dumped: list[dict[str, Any]] = pydantic_ai_messages.ModelMessagesTypeAdapter.dump_python(
        messages,
        mode="json",
    )
    return dumped


def _serialise_usage(
    usage: pydantic_ai_usage.RequestUsage | None,
) -> dict[str, Any] | None:
    """
    Return a JSON-safe representation of *usage*, or ``None`` when no values are set.

    :class:`RequestUsage` is a stdlib ``@dataclass`` so we use
    :func:`dataclasses.asdict` for a deterministic flat dict.  An all-zero
    usage object is treated as "no usage reported" — the canonical bundle
    keeps ``token_usage=None`` in that case to avoid baking misleading
    zero counters into the SHA.
    """
    if usage is None:
        return None
    if not usage.has_values():
        return None
    return dataclasses.asdict(usage)


class CapturingModel(pydantic_ai_wrapper.WrapperModel):
    """
    PydanticAI :class:`WrapperModel` that records every request into the active replay builder.

    Wrap any :class:`Model` instance with this class to get uniform RFC §3.8
    LLM-I/O capture without touching the underlying provider's request
    implementation.  The wrapper delegates to the wrapped model for the
    actual call, then appends an
    :class:`~sentinel.utils.replay_bundle.LLMIOEntry` to the active
    :class:`~sentinel.utils.replay_bundle.ReplayBundleBuilder` (if one is
    bound to the current asyncio context) — runs that aren't being
    captured pay only one ``ContextVar.get()`` per LLM call.

    Streaming requests are not supported by this slice — none of the
    Sentinel pipelines use ``agent.iter()`` / ``request_stream`` today.
    """

    def __init__(self, *, wrapped: Model, agent_name: str) -> None:
        """
        Build a capturing wrapper around *wrapped* tagged with *agent_name*.

        :param wrapped: The underlying :class:`Model` whose calls will be captured.
        :param agent_name: Stable identifier of the agent owning this model
            (e.g. ``"alert_classifier"``).  Recorded onto every captured
            :class:`LLMIOEntry` so the replay CLI can reason about which
            agent issued each call.
        """
        super().__init__(wrapped)
        self._agent_name = agent_name

    async def request(
        self,
        messages: list[pydantic_ai_messages.ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> pydantic_ai_messages.ModelResponse:
        """
        Delegate the request to the wrapped model and capture I/O for replay.

        Failures inside the wrapped model are re-raised unchanged after a
        best-effort capture: the entry's ``outputs`` records the exception's
        string form so replay diffs surface LLM-error drift, and the
        original exception propagates so the pipeline's error path is
        unchanged.
        """
        try:
            response = await self.wrapped.request(
                messages,
                model_settings,
                model_request_parameters,
            )
        except Exception as exc:
            runtime.record_llm_call(
                agent_name=self._agent_name,
                model_id=self.wrapped.model_name,
                inputs={"messages": _serialise_messages(messages)},
                outputs=f"<error: {type(exc).__name__}: {exc}>",
                token_usage=None,
            )
            raise

        runtime.record_llm_call(
            agent_name=self._agent_name,
            model_id=self.wrapped.model_name,
            inputs={"messages": _serialise_messages(messages)},
            outputs=recorded_model_mod.serialise_model_response(response),
            token_usage=_serialise_usage(response.usage),
        )
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[pydantic_ai_messages.ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        """
        Streaming is not supported by the F4.7 capture slice.

        :raises NotImplementedError: always — Sentinel pipelines run agents
            via the non-streaming ``agent.run()`` path; supporting capture
            of streamed responses would require materialising the stream
            before recording, which is not in scope for this slice.
        """
        del messages, model_settings, model_request_parameters, run_context
        if (
            False
        ):  # pragma: no cover -- yield is required to make this a generator for type checking
            yield  # type: ignore[unreachable]
        raise NotImplementedError(
            "CapturingModel does not support streaming requests in the F4.7 slice."
        )
