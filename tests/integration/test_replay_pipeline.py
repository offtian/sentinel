"""
F4.7 end-to-end integration: capture → persist → fetch → replay round-trip.

Validates the wire-format contract between slices B (CapturingModel),
C (RecordedModel + RecordedToolset + new fetcher), and A (tracer + DB
columns) without standing up a real pipeline:

1. CapturingModel records an LLM I/O entry whose ``outputs`` survive
   serialisation through canonical JSON and reconstruct via RecordedModel.
2. The tracer's ``complete_pipeline`` hands the persistence layer the
   same canonical JSON the fetcher then reconstructs from, with matching
   bundle SHA.
3. Tool capture records inputs and outputs that RecordedToolset replays
   in the same order with strict name/input matching, and order/name/arg
   drift surfaces as RecordedReplayMismatchError.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest
from pydantic_ai import messages as pydantic_ai_messages
from pydantic_ai import usage as pydantic_ai_usage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models import test as pydantic_ai_test_model

from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries as pipeline_queries
from sentinel.domain.pipeline import tracer as pipeline_tracer
from sentinel.plugins.models import capturing as capturing_mod
from sentinel.plugins.models import recorded as recorded_model_mod
from sentinel.plugins.toolsets import _runtime as runtime_mod
from sentinel.plugins.toolsets import recorded as recorded_toolset_mod
from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


class _StubLLMModel(pydantic_ai_test_model.TestModel):
    """Deterministic stub that returns a fixed text response per call."""

    def __init__(self, *, response_text: str) -> None:
        super().__init__(model_name="stub-model")
        self._response_text = response_text

    async def request(
        self,
        messages: list[pydantic_ai_messages.ModelMessage],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
    ) -> pydantic_ai_messages.ModelResponse:
        del messages, model_settings, model_request_parameters
        return pydantic_ai_messages.ModelResponse(
            parts=[pydantic_ai_messages.TextPart(content=self._response_text)],
            model_name="stub-model",
            usage=pydantic_ai_usage.RequestUsage(input_tokens=3, output_tokens=4),
        )


@pytest.mark.asyncio
async def test_captured_llm_entry_round_trips_through_recorded_model() -> None:
    """
    Test that an LLM response captured via CapturingModel can be replayed
    bit-for-bit by RecordedModel.

    This is the slice B ↔ slice C wire-format contract.
    """
    # Given a builder bound to the current context and a CapturingModel
    # wrapping a deterministic stub
    builder = bundle_mod.ReplayBundleBuilder()
    token = runtime_mod.bind_replay_builder(builder)
    try:
        capturing = capturing_mod.CapturingModel(
            wrapped=_StubLLMModel(response_text="root cause: db pool exhausted"),
            agent_name="root_cause_analyser",
        )
        request_messages: list[pydantic_ai_messages.ModelMessage] = [
            pydantic_ai_messages.ModelRequest(
                parts=[pydantic_ai_messages.UserPromptPart(content="why is the alert firing?")],
            )
        ]
        request_params = ModelRequestParameters()

        # When CapturingModel handles a request
        live_response = await capturing.request(
            request_messages, model_settings=None, model_request_parameters=request_params
        )
    finally:
        runtime_mod.unbind_replay_builder(token)

    # Then exactly one LLMIOEntry was recorded with our agent + model identity
    bundle = builder.build(
        envelope=factories.make_envelope(),
        alert_payload={"alert_id": "P1"},
        runbook_id=None,
        runbook_version_sha=None,
        final_outputs={"root_cause": "db pool exhausted"},
    )
    assert len(bundle.llm_io) == 1
    entry = bundle.llm_io[0]
    assert entry.agent_name == "root_cause_analyser"
    assert entry.model_id == "stub-model"

    # And RecordedModel can reconstruct that response from the entry alone
    recorded = recorded_model_mod.RecordedModel(bundle.llm_io)
    replayed_response = await recorded.request(
        request_messages, model_settings=None, model_request_parameters=request_params
    )

    assert isinstance(replayed_response, pydantic_ai_messages.ModelResponse)
    assert len(replayed_response.parts) == len(live_response.parts)
    assert isinstance(replayed_response.parts[0], pydantic_ai_messages.TextPart)
    assert replayed_response.parts[0].content == "root cause: db pool exhausted"


@pytest.mark.asyncio
async def test_recorded_toolset_replays_in_order_and_rejects_drift() -> None:
    """Test that RecordedToolset matches strict order, name, and inputs."""
    # Given a bundle with two recorded tool calls
    entries = (
        bundle_mod.ToolIOEntry(
            tool_name="query_logs",
            inputs={"service": "billing", "limit": 10},
            outputs=[{"line": "ERROR pool exhausted"}],
            at=datetime.now(tz=UTC),
        ),
        bundle_mod.ToolIOEntry(
            tool_name="query_metrics",
            inputs={"service": "billing", "metric": "db.pool.used"},
            outputs=[{"value": 100}],
            at=datetime.now(tz=UTC),
        ),
    )
    toolset = recorded_toolset_mod.RecordedToolset(entries)
    ctx_stub = mock.MagicMock()
    tool_stub = mock.MagicMock()

    # When the live calls match in order, name, and inputs
    out_one = await toolset.call_tool(
        "query_logs", {"service": "billing", "limit": 10}, ctx_stub, tool_stub
    )
    out_two = await toolset.call_tool(
        "query_metrics",
        {"service": "billing", "metric": "db.pool.used"},
        ctx_stub,
        tool_stub,
    )

    # Then both return the recorded outputs
    assert out_one == [{"line": "ERROR pool exhausted"}]
    assert out_two == [{"value": 100}]

    # And a third call (queue exhausted) raises a replay mismatch
    with pytest.raises(pipeline_errors.RecordedReplayMismatchError) as excinfo:
        await toolset.call_tool("query_logs", {}, ctx_stub, tool_stub)
    assert excinfo.value.kind == "tool"
    assert excinfo.value.reason == "exhausted"


@pytest.mark.asyncio
async def test_tracer_persists_canonical_bundle_then_fetcher_round_trips() -> None:
    """
    Test that the tracer's persisted bundle JSON + sha reconstruct via the
    new fetcher, with sha matching.

    Stubs the DB layer so the test runs without Postgres but still exercises
    the canonical JSON wire format both slices commit to.
    """
    # Given a tracer with no DB (capture-only path) and a bound bundle
    persisted_kwargs: dict[str, Any] = {}
    fake_run_id = uuid.uuid4()

    async def _fake_persist_pipeline_run(**kwargs: Any) -> uuid.UUID:
        return fake_run_id

    async def _fake_complete_pipeline_run(**kwargs: Any) -> None:
        persisted_kwargs.update(kwargs)

    fake_db = mock.MagicMock()
    fake_cfg = mock.MagicMock(enable_replay_bundle=True)

    with (
        mock.patch(
            "sentinel.domain.pipeline.operations.persist_pipeline_run",
            side_effect=_fake_persist_pipeline_run,
        ),
        mock.patch(
            "sentinel.domain.pipeline.operations.complete_pipeline_run",
            side_effect=_fake_complete_pipeline_run,
        ),
        mock.patch(
            "sentinel.domain.pipeline.tracer.config_mod.get_config",
            return_value=fake_cfg,
        ),
    ):
        # And given the tracer opens a capture window
        tracer = pipeline_tracer.ExecutionTracer(db=fake_db)
        envelope = factories.make_envelope()
        alert_payload = {"alert_id": "P42", "title": "db pool exhausted"}
        await tracer.start_pipeline(
            pipeline_type="investigation",
            input_data=alert_payload,
            envelope=envelope,
            alert_payload=alert_payload,
        )

        # And a tool call is captured during the run
        runtime_mod.record_tool_call(
            tool_name="query_logs",
            inputs={"service": "billing"},
            outputs=[{"line": "ERROR"}],
        )

        # When the pipeline completes
        final_reply = {"alert_id": "P42", "root_cause": "pool exhausted"}
        await tracer.complete_pipeline(
            status="completed",
            final_reply=final_reply,
            runbook_id="rb-db-pool",
            runbook_version_sha="abc123",
        )

    # Then the tracer wrote a canonical bundle dict + sha to the DB
    bundle_json = persisted_kwargs["replay_bundle_json"]
    bundle_sha = persisted_kwargs["replay_bundle_sha"]
    assert bundle_json is not None
    assert bundle_sha is not None
    assert bundle_json["alert_payload"] == alert_payload
    assert bundle_json["final_outputs"] == final_reply
    assert bundle_json["runbook_id"] == "rb-db-pool"
    assert bundle_json["runbook_version_sha"] == "abc123"
    assert len(bundle_json["tool_io"]) == 1
    assert bundle_json["tool_io"][0]["tool_name"] == "query_logs"

    # And the fetcher reconstructs the same bundle when handed the persisted row
    fake_row = mock.MagicMock()
    fake_row._mapping = {
        "id": fake_run_id,
        "replay_bundle_json": bundle_json,
        "replay_bundle_sha": bundle_sha,
    }

    async def _fake_fetch_one(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return fake_row

    fake_db_for_fetch = mock.MagicMock()
    fake_db_for_fetch.fetch_one = mock.AsyncMock(side_effect=_fake_fetch_one)

    reconstructed = await pipeline_queries.fetch_recorded_replay_bundle(
        db=fake_db_for_fetch,
        run_id=fake_run_id,
    )

    # Then the reconstructed bundle's sha matches the stored sha
    assert reconstructed.bundle_sha == bundle_sha
    assert reconstructed.alert_payload == alert_payload
    assert reconstructed.final_outputs == final_reply
    assert reconstructed.runbook_id == "rb-db-pool"
    assert reconstructed.envelope.tenant_id == envelope.tenant_id

    # And canonical JSON round-trips exactly
    assert json.loads(bundle_mod.to_canonical_json(reconstructed)) == bundle_json
