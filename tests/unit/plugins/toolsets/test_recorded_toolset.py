"""Tests for the RecordedToolset replay primitive (F4.7)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.plugins.toolsets import recorded as recorded_toolset_mod
from sentinel.utils import replay_bundle as bundle_mod


_FIXED_AT = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def _make_tool_entry(
    *,
    tool_name: str,
    inputs: dict[str, object] | None = None,
    outputs: object = None,
) -> bundle_mod.ToolIOEntry:
    return bundle_mod.ToolIOEntry(
        tool_name=tool_name,
        inputs=inputs or {},
        outputs=outputs,
        evidence_object_id=None,
        at=_FIXED_AT,
    )


async def _call(
    *,
    toolset: recorded_toolset_mod.RecordedToolset,
    name: str,
    tool_args: dict[str, object],
) -> object:
    return await toolset.call_tool(name, tool_args, mock.sentinel.ctx, mock.sentinel.tool)


class TestRecordedToolsetOrder:
    @pytest.mark.asyncio
    async def test_returns_recorded_outputs_in_invocation_order(self) -> None:
        # Given a RecordedToolset seeded with three matching entries
        entries = (
            _make_tool_entry(tool_name="alpha", inputs={"i": 1}, outputs="a"),
            _make_tool_entry(tool_name="beta", inputs={"i": 2}, outputs="b"),
            _make_tool_entry(tool_name="gamma", inputs={"i": 3}, outputs="c"),
        )
        toolset = recorded_toolset_mod.RecordedToolset(entries)

        # When the toolset is invoked three times in order with matching args
        first = await _call(toolset=toolset, name="alpha", tool_args={"i": 1})
        second = await _call(toolset=toolset, name="beta", tool_args={"i": 2})
        third = await _call(toolset=toolset, name="gamma", tool_args={"i": 3})

        # Then each call returns the recorded output for its slot
        assert (first, second, third) == ("a", "b", "c")


class TestRecordedToolsetMismatch:
    @pytest.mark.asyncio
    async def test_raises_on_tool_name_mismatch(self) -> None:
        # Given a RecordedToolset whose first entry is named "expected"
        entries = (_make_tool_entry(tool_name="expected", outputs="ok"),)
        toolset = recorded_toolset_mod.RecordedToolset(entries)

        # When a different tool name is invoked
        # Then RecordedReplayMismatchError is raised with kind="tool_name"
        with pytest.raises(pipeline_errors.RecordedReplayMismatchError) as exc_info:
            await _call(toolset=toolset, name="other", tool_args={})
        assert exc_info.value.kind == "tool_name"
        assert exc_info.value.expected == "expected"
        assert exc_info.value.actual == "other"

    @pytest.mark.asyncio
    async def test_raises_on_tool_args_mismatch(self) -> None:
        # Given a RecordedToolset whose first entry expects inputs {"x": 1}
        entries = (_make_tool_entry(tool_name="probe", inputs={"x": 1}, outputs="ok"),)
        toolset = recorded_toolset_mod.RecordedToolset(entries)

        # When the toolset is called with mismatching args {"x": 2}
        # Then RecordedReplayMismatchError is raised with kind="tool_args"
        with pytest.raises(pipeline_errors.RecordedReplayMismatchError) as exc_info:
            await _call(toolset=toolset, name="probe", tool_args={"x": 2})
        assert exc_info.value.kind == "tool_args"
        assert exc_info.value.expected == {"x": 1}
        assert exc_info.value.actual == {"x": 2}

    @pytest.mark.asyncio
    async def test_raises_on_exhaustion(self) -> None:
        # Given an empty RecordedToolset
        toolset = recorded_toolset_mod.RecordedToolset(())

        # When call_tool is invoked
        # Then RecordedReplayMismatchError is raised with kind="tool", reason="exhausted"
        with pytest.raises(pipeline_errors.RecordedReplayMismatchError) as exc_info:
            await _call(toolset=toolset, name="anything", tool_args={})
        assert exc_info.value.kind == "tool"
        assert exc_info.value.reason == "exhausted"
        assert exc_info.value.expected == "anything"
