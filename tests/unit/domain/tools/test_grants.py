"""
Unit tests for domain/tools/grants.py (F7 capability enforcement).

Tests the pure authorize_tool_call() function and RunbookGrant token shape.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.domain.runbooks import models as runbook_models
from sentinel.domain.tools import grants as grants_mod
from tests import factories


def _make_tools_config(
    *,
    allowed: tuple[str, ...] = ("k8s_get_pod_logs", "k8s_get_events"),
    denied: tuple[str, ...] = (),
    max_total: int = 20,
) -> runbook_models.ToolsConfig:
    return runbook_models.ToolsConfig(
        allowed_tools=tuple(runbook_models.ToolSpec(name=n, max_calls=5) for n in allowed),
        denied_tools=denied,
        max_total_tool_calls=max_total,
        max_loop_iterations=10,
    )


class TestAuthorizeToolCall:
    def test_raises_tool_not_in_runbook_when_tool_absent(self) -> None:
        # Given a runbook that only allows k8s tools
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",), denied_tools=())

        # When authorizing a tool not listed in the runbook
        with pytest.raises(grants_mod.ToolNotInRunbookError) as exc_info:
            grants_mod.authorize_tool_call(
                runbook=runbook,
                tool_name="prom_query_range",
                tenant_id="pm-a",
            )

        # Then the error identifies the tool and runbook
        err = exc_info.value
        assert err.tool_name == "prom_query_range"
        assert err.runbook_id == runbook.runbook_id

    def test_raises_tool_not_in_runbook_when_tool_explicitly_denied(self) -> None:
        # Given a runbook that explicitly denies prom_query_range
        runbook = factories.make_runbook(
            allowed_tools=("k8s_get_pod_logs",),
            denied_tools=("prom_query_range",),
        )

        # When authorizing the denied tool
        with pytest.raises(grants_mod.ToolNotInRunbookError) as exc_info:
            grants_mod.authorize_tool_call(
                runbook=runbook,
                tool_name="prom_query_range",
                tenant_id="pm-a",
            )

        # Then the error carries the tool name
        assert exc_info.value.tool_name == "prom_query_range"

    def test_raises_tenant_scope_violation_when_namespace_differs(self) -> None:
        # Given a runbook that allows k8s_get_pod_logs and an envelope for pm-a
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",))

        # When the call's namespace belongs to a different tenant
        with pytest.raises(grants_mod.TenantScopeViolationError) as exc_info:
            grants_mod.authorize_tool_call(
                runbook=runbook,
                tool_name="k8s_get_pod_logs",
                tenant_id="pm-a",
                call_namespace="other-pm",
            )

        # Then the error records both tenants
        err = exc_info.value
        assert err.tenant_id == "pm-a"
        assert err.call_namespace == "other-pm"

    def test_returns_grant_when_tool_listed_and_tenant_matches(self) -> None:
        # Given a runbook allowing k8s_get_pod_logs and a matching tenant+namespace
        runbook = factories.make_runbook(allowed_tools=("k8s_get_pod_logs",))
        fixed_now = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)

        # When authorizing correctly
        grant = grants_mod.authorize_tool_call(
            runbook=runbook,
            tool_name="k8s_get_pod_logs",
            tenant_id="pm-a",
            call_namespace="pm-a",
            now=fixed_now,
        )

        # Then a RunbookGrant is returned
        assert isinstance(grant, grants_mod.RunbookGrant)

    def test_none_namespace_is_permitted_for_authorized_tool(self) -> None:
        # Given a runbook allowing k8s_get_events (a tool with no namespace arg)
        runbook = factories.make_runbook(allowed_tools=("k8s_get_events",))

        # When the tool is called without a namespace (cluster-wide query)
        grant = grants_mod.authorize_tool_call(
            runbook=runbook,
            tool_name="k8s_get_events",
            tenant_id="pm-a",
            call_namespace=None,
        )

        # Then the grant is issued (namespace=None bypasses tenant check)
        assert isinstance(grant, grants_mod.RunbookGrant)

    def test_grant_carries_all_five_fields(self) -> None:
        # Given a runbook with a known content_sha and a fixed clock
        runbook = factories.make_runbook(
            runbook_id="k8s-crashloop",
            content_sha="abc123",
            allowed_tools=("k8s_get_pod_logs",),
        )
        fixed_now = datetime(2026, 4, 29, 9, 0, tzinfo=UTC)

        # When the grant is issued
        grant = grants_mod.authorize_tool_call(
            runbook=runbook,
            tool_name="k8s_get_pod_logs",
            tenant_id="pm-alpha",
            now=fixed_now,
        )

        # Then all five fields are populated correctly
        assert grant.runbook_id == "k8s-crashloop"
        assert grant.runbook_content_sha == "abc123"
        assert grant.tool_name == "k8s_get_pod_logs"
        assert grant.tenant_id == "pm-alpha"
        assert grant.granted_at == fixed_now
