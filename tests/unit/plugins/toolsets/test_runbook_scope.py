"""
Unit tests for plugins/toolsets/_runbook_scope.py (F7 wrapper).

Tests RunbookScopedToolset: per-tool authorization, budget enforcement,
soft-fail when no runbook, and the wrap_for_runbook_scope factory.
"""

from __future__ import annotations

import types
from typing import Any
from unittest import mock

import pytest

from sentinel.domain.tools import grants as grants_mod
from sentinel.plugins.toolsets import _runbook_scope as scope_mod
from tests import factories


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubToolset:
    """Minimal AbstractToolset stub that returns a canned value or raises."""

    def __init__(self, *, result: Any = "ok", raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._result

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        return {}

    @property
    def id(self) -> str | None:
        return "stub"


def _make_ctx(
    *,
    runbook: Any = None,
    tenant_id: str = "pm-a",
    counters: dict[str, int] | None = None,
) -> types.SimpleNamespace:
    envelope = factories.make_envelope(tenant_id=tenant_id)
    deps = types.SimpleNamespace(
        runbook=runbook,
        envelope=envelope,
        _tool_call_counters=counters if counters is not None else {},
    )
    return types.SimpleNamespace(deps=deps)


# ---------------------------------------------------------------------------
# RunbookScopedToolset
# ---------------------------------------------------------------------------


class TestRunbookScopedToolsetAuthorization:
    async def test_raises_tool_not_in_runbook_on_unauthorized_tool(self) -> None:
        # Given a runbook that only allows k8s tools and an unauthorized call
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",))
        ctx = _make_ctx(runbook=runbook)
        audit_fn = mock.AsyncMock(return_value=None)
        wrapper = scope_mod.RunbookScopedToolset(_StubToolset(), label="test", audit_fn=audit_fn)

        # When an unauthorized tool is called
        with pytest.raises(grants_mod.ToolNotInRunbookError):
            await wrapper.call_tool("prom_query_range", {}, ctx, mock.sentinel.tool)

        # Then the audit function is called once
        audit_fn.assert_called_once()
        call_kwargs = audit_fn.call_args.kwargs
        assert call_kwargs["action"] == "tool_call_rejected"
        assert call_kwargs["resource_id"] == "prom_query_range"

    async def test_raises_tenant_scope_violation_on_cross_tenant_namespace(
        self,
    ) -> None:
        # Given a runbook allowing k8s_get_pod_logs and a cross-tenant namespace
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",))
        ctx = _make_ctx(runbook=runbook, tenant_id="pm-a")
        audit_fn = mock.AsyncMock(return_value=None)
        wrapper = scope_mod.RunbookScopedToolset(_StubToolset(), label="test", audit_fn=audit_fn)

        # When the tool is called with a different tenant's namespace
        with pytest.raises(grants_mod.TenantScopeViolationError):
            await wrapper.call_tool(
                "k8s_get_pod_logs", {"namespace": "other-pm"}, ctx, mock.sentinel.tool
            )

        # Then the audit function is called once
        audit_fn.assert_called_once()

    async def test_delegates_to_wrapped_toolset_on_authorized_call(self) -> None:
        # Given an authorized call (tool listed, same-tenant namespace)
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",))
        ctx = _make_ctx(runbook=runbook, tenant_id="pm-a")
        stub = _StubToolset(result="pod-logs-output")
        wrapper = scope_mod.RunbookScopedToolset(stub, label="test", audit_fn=None)

        # When the tool is called correctly
        result = await wrapper.call_tool(
            "k8s_get_pod_logs", {"namespace": "pm-a"}, ctx, mock.sentinel.tool
        )

        # Then the wrapped toolset's result is returned
        assert result == "pod-logs-output"

    async def test_counter_increments_on_authorized_call(self) -> None:
        # Given an authorized call with an empty counter dict
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",))
        counters: dict[str, int] = {}
        ctx = _make_ctx(runbook=runbook, counters=counters)
        wrapper = scope_mod.RunbookScopedToolset(_StubToolset(), label="test")

        # When the tool is called
        await wrapper.call_tool("k8s_get_pod_logs", {}, ctx, mock.sentinel.tool)

        # Then the counter is incremented
        assert counters["k8s_get_pod_logs"] == 1

    async def test_passes_through_when_runbook_is_none(self) -> None:
        # Given no active runbook (no-match / pre-F6 path — soft-fail mode)
        ctx = _make_ctx(runbook=None)
        stub = _StubToolset(result="passthrough-ok")
        wrapper = scope_mod.RunbookScopedToolset(stub, label="test", audit_fn=None)

        # When any tool is called without a runbook
        result = await wrapper.call_tool("prom_query_range", {}, ctx, mock.sentinel.tool)

        # Then the call passes through without a capability check
        assert result == "passthrough-ok"


class TestRunbookScopedToolsetBudget:
    async def test_raises_budget_exceeded_on_per_tool_cap(self) -> None:
        # Given a runbook with per-tool max_calls=2 and a counter already at 2
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",), tool_max_calls=2)
        counters = {"k8s_get_pod_logs": 2}
        ctx = _make_ctx(runbook=runbook, counters=counters)
        audit_fn = mock.AsyncMock(return_value=None)
        wrapper = scope_mod.RunbookScopedToolset(_StubToolset(), label="test", audit_fn=audit_fn)

        # When the tool is called again (would exceed the cap)
        with pytest.raises(grants_mod.ToolBudgetExceededError) as exc_info:
            await wrapper.call_tool("k8s_get_pod_logs", {}, ctx, mock.sentinel.tool)

        # Then the error reports per_tool scope and audit is called
        assert exc_info.value.scope == "per_tool"
        audit_fn.assert_called_once()

    async def test_raises_budget_exceeded_on_total_cap(self) -> None:
        # Given a runbook with max_total_tool_calls=3 and two different tools used
        runbook = factories.make_runbook(
            allowed_tools=("k8s_get_pod_logs", "k8s_get_events"),
            max_total_tool_calls=3,
            tool_max_calls=10,
        )
        counters = {"k8s_get_pod_logs": 2, "k8s_get_events": 1}
        ctx = _make_ctx(runbook=runbook, counters=counters)
        audit_fn = mock.AsyncMock(return_value=None)
        wrapper = scope_mod.RunbookScopedToolset(_StubToolset(), label="test", audit_fn=audit_fn)

        # When a 4th tool call is attempted (total=3, cap=3)
        with pytest.raises(grants_mod.ToolBudgetExceededError) as exc_info:
            await wrapper.call_tool("k8s_get_pod_logs", {}, ctx, mock.sentinel.tool)

        # Then the error reports total scope and audit is called
        assert exc_info.value.scope == "total"
        audit_fn.assert_called_once()


class TestWrapForRunbookScope:
    def test_wraps_a_plain_toolset(self) -> None:
        # Given an unwrapped toolset
        stub = _StubToolset()

        # When wrapped for runbook scope
        wrapped = scope_mod.wrap_for_runbook_scope(stub, label="k8s")

        # Then a RunbookScopedToolset is returned
        assert isinstance(wrapped, scope_mod.RunbookScopedToolset)

    def test_is_idempotent_for_already_wrapped_toolset(self) -> None:
        # Given an already-wrapped toolset
        stub = _StubToolset()
        first = scope_mod.wrap_for_runbook_scope(stub)

        # When wrapped again
        second = scope_mod.wrap_for_runbook_scope(first)

        # Then the same instance is returned (no double-wrapping)
        assert second is first

    def test_returns_none_for_none_input(self) -> None:
        # Given a None toolset (optional toolset not configured)
        # When wrapped
        result = scope_mod.wrap_for_runbook_scope(None)

        # Then None is returned unchanged
        assert result is None
