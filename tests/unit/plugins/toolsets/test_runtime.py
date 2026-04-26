"""Tests for the toolset replay-capture runtime (F4.6)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest
from pydantic_ai.toolsets import FunctionToolset

from sentinel.plugins.toolsets import _runtime as runtime_mod
from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


def _build_bundle(builder: bundle_mod.ReplayBundleBuilder) -> bundle_mod.ReplayBundle:
    """Materialise a bundle with placeholder envelope/alert for assertions."""
    return builder.build(
        envelope=factories.make_envelope(),
        alert_payload={},
        runbook_id=None,
        runbook_version_sha=None,
        final_outputs={},
    )


class TestCurrentReplayBuilder:
    def test_returns_none_when_unbound(self):
        # Given no builder bound to the ContextVar
        # When current_replay_builder is called
        # Then it returns None
        assert runtime_mod.current_replay_builder() is None

    def test_returns_bound_builder(self):
        # Given a freshly bound builder
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When current_replay_builder is called
            # Then it returns the bound builder
            assert runtime_mod.current_replay_builder() is builder
        finally:
            runtime_mod.unbind_replay_builder(token)

    def test_unbind_restores_previous_state(self):
        # Given a builder bound and then unbound
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        runtime_mod.unbind_replay_builder(token)

        # When current_replay_builder is called
        # Then it returns None again
        assert runtime_mod.current_replay_builder() is None


class TestRecordToolCall:
    def test_is_noop_when_no_builder_bound(self):
        # Given no builder bound
        # When record_tool_call is invoked
        # Then nothing is raised and there's no global side effect
        runtime_mod.record_tool_call(
            tool_name="kubectl_logs",
            inputs={"namespace": "ns"},
            outputs="ok",
            at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        )
        assert runtime_mod.current_replay_builder() is None

    def test_appends_tool_entry_to_active_builder(self):
        # Given an active builder
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When a tool call is recorded
            runtime_mod.record_tool_call(
                tool_name="query_recent_logs",
                inputs={"service": "api", "minutes_back": 30},
                outputs="200 lines",
                at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
            )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # Then the builder has captured the tool entry
        bundle = _build_bundle(builder)
        assert len(bundle.tool_io) == 1
        recorded = bundle.tool_io[0]
        assert recorded.tool_name == "query_recent_logs"
        assert recorded.inputs == {"service": "api", "minutes_back": 30}
        assert recorded.outputs == "200 lines"

    def test_preserves_invocation_order_for_serial_calls(self):
        # Given an active builder
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When three tool calls are recorded in sequence
            for index, name in enumerate(("first", "second", "third"), start=1):
                runtime_mod.record_tool_call(
                    tool_name=name,
                    inputs={"i": index},
                    outputs=index,
                    at=datetime(2026, 4, 25, 12, 0, index, tzinfo=UTC),
                )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # Then the recorded entries preserve insertion order
        bundle = _build_bundle(builder)
        assert tuple(e.tool_name for e in bundle.tool_io) == (
            "first",
            "second",
            "third",
        )


class TestContextVarIsolation:
    async def test_each_task_sees_only_its_own_builder_under_gather(self):
        # Given two independent builders bound inside two tasks running
        # concurrently via asyncio.gather
        async def task_with_builder(name: str) -> bundle_mod.ReplayBundleBuilder:
            local_builder = bundle_mod.ReplayBundleBuilder()
            token = runtime_mod.bind_replay_builder(local_builder)
            try:
                # Yield to give the other task a chance to interleave
                await asyncio.sleep(0)
                runtime_mod.record_tool_call(
                    tool_name=name,
                    inputs={"task": name},
                    outputs=name,
                    at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
                )
                await asyncio.sleep(0)
                runtime_mod.record_tool_call(
                    tool_name=f"{name}-second",
                    inputs={},
                    outputs=None,
                    at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
                )
            finally:
                runtime_mod.unbind_replay_builder(token)
            return local_builder

        # When the two tasks run concurrently
        first_builder, second_builder = await asyncio.gather(
            task_with_builder("alpha"),
            task_with_builder("beta"),
        )

        # Then each builder only sees the entries from its own task
        first_bundle = _build_bundle(first_builder)
        second_bundle = _build_bundle(second_builder)

        first_names = tuple(e.tool_name for e in first_bundle.tool_io)
        second_names = tuple(e.tool_name for e in second_bundle.tool_io)

        assert first_names == ("alpha", "alpha-second")
        assert second_names == ("beta", "beta-second")

    async def test_outer_task_sees_no_entries_recorded_in_inner_task(self):
        # Given a builder bound at outer scope and an inner task that
        # binds its own builder
        outer_builder = bundle_mod.ReplayBundleBuilder()
        outer_token = runtime_mod.bind_replay_builder(outer_builder)
        try:

            async def inner() -> bundle_mod.ReplayBundleBuilder:
                inner_builder = bundle_mod.ReplayBundleBuilder()
                inner_token = runtime_mod.bind_replay_builder(inner_builder)
                try:
                    runtime_mod.record_tool_call(
                        tool_name="inner_only",
                        inputs={},
                        outputs=None,
                        at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
                    )
                finally:
                    runtime_mod.unbind_replay_builder(inner_token)
                return inner_builder

            inner_builder = await asyncio.create_task(inner())

            # When the outer scope records a separate tool call
            runtime_mod.record_tool_call(
                tool_name="outer_only",
                inputs={},
                outputs=None,
                at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
            )
        finally:
            runtime_mod.unbind_replay_builder(outer_token)

        # Then the outer builder only saw the outer call and the inner
        # builder only saw the inner call
        outer_bundle = _build_bundle(outer_builder)
        inner_bundle = _build_bundle(inner_builder)
        assert tuple(e.tool_name for e in outer_bundle.tool_io) == ("outer_only",)
        assert tuple(e.tool_name for e in inner_bundle.tool_io) == ("inner_only",)


class TestFlushSerialisation:
    def test_flush_returns_immutable_bundle_and_unbinds_builder(self):
        # Given a bound builder with a recorded tool call
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        runtime_mod.record_tool_call(
            tool_name="kubectl_logs",
            inputs={"namespace": "ns"},
            outputs="lines",
            at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        )

        # When flush_replay_capture is invoked with envelope / alert /
        # outputs and the bind token
        bundle = runtime_mod.flush_replay_capture(
            token=token,
            envelope=factories.make_envelope(),
            alert_payload={"alert_id": "P1"},
            runbook_id="k8s-crashloop",
            runbook_version_sha="v1",
            final_outputs={"root_cause": "OOM"},
        )

        # Then a frozen bundle is returned and the ContextVar is reset
        assert bundle is not None
        assert bundle.tool_io[0].tool_name == "kubectl_logs"
        assert bundle.runbook_id == "k8s-crashloop"
        assert runtime_mod.current_replay_builder() is None

    def test_flush_returns_none_when_no_builder_bound(self):
        # Given no builder bound and a sentinel "no token" value
        # When flush_replay_capture is invoked with token=None
        bundle = runtime_mod.flush_replay_capture(
            token=None,
            envelope=factories.make_envelope(),
            alert_payload={},
            runbook_id=None,
            runbook_version_sha=None,
            final_outputs={},
        )

        # Then no bundle is produced
        assert bundle is None


class _FakeToolset(FunctionToolset[Any]):
    """
    Minimal AbstractToolset double for unit-testing :class:`ReplayCapturingToolset`.

    The wrapper only ever calls ``self.wrapped.call_tool(...)`` so a thin
    subclass overriding ``call_tool`` to honour a behaviour map is enough.
    Sub-classing :class:`FunctionToolset` keeps mypy happy with the
    :class:`AbstractToolset` type bound that ``WrapperToolset`` requires.
    """

    def __init__(self, *, behaviours: dict[str, Any]) -> None:
        super().__init__()
        self._behaviours = behaviours

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: Any,
    ) -> Any:
        del ctx, tool  # unused — wrapper only delegates
        behaviour = self._behaviours[name]
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour(tool_args)


async def _call_through_wrapper(
    *,
    wrapper: runtime_mod.ReplayCapturingToolset,
    tool_name: str,
    tool_args: dict[str, Any],
) -> Any:
    """Invoke the wrapper's ``call_tool`` directly with sentinel ctx/tool."""
    return await wrapper.call_tool(tool_name, tool_args, mock.sentinel.ctx, mock.sentinel.tool)


class TestReplayCapturingToolset:
    async def test_records_tool_call_into_active_builder(self):
        # Given a fake toolset wrapped for replay capture and a bound builder
        wrapper = runtime_mod.ReplayCapturingToolset(
            _FakeToolset(behaviours={"echo": lambda args: f"echo:{args['value']}"}),
        )
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When the wrapper is invoked
            result = await _call_through_wrapper(
                wrapper=wrapper,
                tool_name="echo",
                tool_args={"value": "hello"},
            )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # Then the underlying tool ran and the call was recorded
        assert result == "echo:hello"
        bundle = _build_bundle(builder)
        assert len(bundle.tool_io) == 1
        assert bundle.tool_io[0].tool_name == "echo"
        assert bundle.tool_io[0].inputs == {"value": "hello"}
        assert bundle.tool_io[0].outputs == "echo:hello"

    async def test_records_failure_and_reraises(self):
        # Given a fake toolset whose tool raises and a bound builder
        wrapper = runtime_mod.ReplayCapturingToolset(
            _FakeToolset(behaviours={"boom": RuntimeError("kaboom")}),
        )
        builder = bundle_mod.ReplayBundleBuilder()
        token = runtime_mod.bind_replay_builder(builder)
        try:
            # When the failing tool is invoked through the wrapper
            with pytest.raises(RuntimeError, match="kaboom"):
                await _call_through_wrapper(
                    wrapper=wrapper,
                    tool_name="boom",
                    tool_args={},
                )
        finally:
            runtime_mod.unbind_replay_builder(token)

        # Then the captured entry's outputs records the error and the original
        # exception propagates (asserted by pytest.raises above)
        bundle = _build_bundle(builder)
        assert len(bundle.tool_io) == 1
        assert bundle.tool_io[0].tool_name == "boom"
        assert "RuntimeError" in str(bundle.tool_io[0].outputs)
        assert "kaboom" in str(bundle.tool_io[0].outputs)

    async def test_is_noop_capture_when_no_builder_bound(self):
        # Given a wrapped toolset and no builder bound
        wrapper = runtime_mod.ReplayCapturingToolset(
            _FakeToolset(behaviours={"echo": lambda args: f"echo:{args['value']}"}),
        )
        assert runtime_mod.current_replay_builder() is None

        # When the wrapper is invoked
        result = await _call_through_wrapper(
            wrapper=wrapper,
            tool_name="echo",
            tool_args={"value": "noop"},
        )

        # Then the tool ran but no capture happened
        assert result == "echo:noop"
        assert runtime_mod.current_replay_builder() is None


class TestWrapForReplay:
    def test_returns_none_when_input_is_none(self):
        # Given a None toolset
        # When wrap_for_replay is called
        # Then it returns None
        assert runtime_mod.wrap_for_replay(None) is None

    def test_wraps_a_function_toolset(self):
        # Given a plain FunctionToolset
        toolset = _FakeToolset(behaviours={})

        # When it is wrapped for replay
        wrapped = runtime_mod.wrap_for_replay(toolset, label="echo-toolset")

        # Then the wrapped instance is a ReplayCapturingToolset around the original
        assert isinstance(wrapped, runtime_mod.ReplayCapturingToolset)
        assert wrapped.wrapped is toolset

    def test_is_idempotent_on_already_wrapped_toolset(self):
        # Given a toolset already wrapped for replay
        wrapped_once = runtime_mod.wrap_for_replay(_FakeToolset(behaviours={}))

        # When wrap_for_replay is called again on the wrapped instance
        wrapped_twice = runtime_mod.wrap_for_replay(wrapped_once)

        # Then no double-wrap occurs (returns the same instance)
        assert wrapped_twice is wrapped_once
