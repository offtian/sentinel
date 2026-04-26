"""
Unit tests for the LangGraph ``with_envelope`` node decorator.

The decorator is the LangGraph counterpart to ``run_node_with_envelope``
from ``interfaces/graphs/_node_helpers.py`` (legacy harness). It binds the
envelope's structlog context and sets the six envelope-owned mandatory OTel
span attributes for the duration of the wrapped node.

These tests cover the contract end-to-end without exercising LangGraph's
runtime; the decorator is framework-agnostic by design.
"""

from __future__ import annotations

from typing import TypedDict
from unittest import mock

import pytest
import structlog
from opentelemetry import trace as otel_trace

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.interfaces.workflows import _envelope as workflows_envelope
from tests import factories


class _SpyState(TypedDict, total=False):
    envelope: envelope_mod.Envelope
    captured_context: dict[str, str]
    extra: int


@pytest.fixture(autouse=True)
def _reset_structlog_context() -> object:
    # Given each test deserves a clean structlog contextvars baseline so
    # leaking bindings from prior tests do not pollute assertions.
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


class TestWithEnvelope:
    @pytest.mark.asyncio
    async def test_calls_wrapped_function_and_returns_its_result(self) -> None:
        # Given a wrapped async node that returns a partial state dict
        envelope = factories.make_envelope()
        call_count = {"n": 0}

        async def node_fn(state: _SpyState) -> dict:
            call_count["n"] += 1
            return {"extra": 7}

        wrapped = workflows_envelope.with_envelope(node_fn)
        state: _SpyState = {"envelope": envelope}

        # When the wrapped node is awaited
        result = await wrapped(state)

        # Then the wrapped function ran exactly once and its return value flows through unchanged
        assert call_count["n"] == 1
        assert result == {"extra": 7}

    @pytest.mark.asyncio
    async def test_binds_log_context_from_envelope(self) -> None:
        # Given a wrapped node that captures the structlog contextvars at run time
        envelope = factories.make_envelope(
            tenant_id="pm-alpha",
            cluster_id="prod-eu-west-1",
            region="eu-west-1",
        )
        captured: dict[str, object] = {}

        async def node_fn(state: _SpyState) -> dict:
            captured.update(structlog.contextvars.get_contextvars())
            return {}

        wrapped = workflows_envelope.with_envelope(node_fn)
        state: _SpyState = {"envelope": envelope}

        # When the wrapped node is awaited
        await wrapped(state)

        # Then every envelope log-context key was bound while the node ran
        for key, value in envelope.to_log_context().items():
            assert captured.get(key) == value
        # And the bindings are removed after the node completes (auto-cleanup)
        post_run = structlog.contextvars.get_contextvars()
        for key in envelope.to_log_context():
            assert key not in post_run

    @pytest.mark.asyncio
    async def test_sets_span_attributes_from_envelope(self) -> None:
        # Given a wrapped node and a fake current span
        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()

        async def node_fn(state: _SpyState) -> dict:
            return {}

        wrapped = workflows_envelope.with_envelope(node_fn)
        state: _SpyState = {"envelope": envelope}

        # When the wrapped node runs with the patched span accessor
        with mock.patch.object(
            workflows_envelope.otel_trace, "get_current_span", return_value=fake_span
        ):
            await wrapped(state)

        # Then the six envelope-owned mandatory attributes are set on the span exactly once
        fake_span.set_attributes.assert_called_once_with(envelope.to_span_attributes())

    @pytest.mark.asyncio
    async def test_propagates_exception_from_wrapped_function(self) -> None:
        # Given a wrapped node that raises a domain error
        envelope = factories.make_envelope()

        async def failing_node(state: _SpyState) -> dict:
            raise RuntimeError("boom")

        wrapped = workflows_envelope.with_envelope(failing_node)
        state: _SpyState = {"envelope": envelope}

        # When the wrapped node is awaited
        # Then the exception propagates unchanged (no swallow, no wrap)
        with pytest.raises(RuntimeError, match="boom"):
            await wrapped(state)

        # And the structlog context is unbound after the failure
        post_run = structlog.contextvars.get_contextvars()
        for key in envelope.to_log_context():
            assert key not in post_run

    def test_imports_otel_trace_at_module_level(self) -> None:
        # Given the decorator module
        # When inspecting its top-level attributes
        # Then opentelemetry.trace is imported as a module (per python.md import rules)
        assert workflows_envelope.otel_trace is otel_trace
