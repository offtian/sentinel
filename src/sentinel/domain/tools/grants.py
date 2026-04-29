"""
Runbook-scoped tool authorization (F7).

Provides the :func:`authorize_tool_call` pure function that validates a tool
invocation against the active runbook's allowed-tool list and the envelope's
tenant identity, then returns an immutable :class:`RunbookGrant` audit token.

Enforcement happens at the **toolset wrapper boundary**
(:class:`~sentinel.plugins.toolsets._runbook_scope.RunbookScopedToolset`),
not at function entry — function-entry checks are bypassable by indirect
prompt injection routes that re-enter the toolset
(Cerbos / OWASP / SuperTokens guidance; F6 contract update §F7.2).
"""

from __future__ import annotations

from datetime import UTC, datetime

import attrs

from sentinel.domain.runbooks import models as runbook_models


# ---------------------------------------------------------------------------
# Grant token
# ---------------------------------------------------------------------------


@attrs.frozen(kw_only=True, slots=True)
class RunbookGrant:
    """
    Immutable audit token produced when a tool call is authorized.

    Stamped onto the ``agent_calls.capability_token`` column (F3.8) so the
    audit trail records exactly which runbook version authorised the call,
    for which tenant, and at what wall-clock time.
    """

    runbook_id: str
    runbook_content_sha: str
    tool_name: str
    tenant_id: str
    granted_at: datetime

    def __str__(self) -> str:
        return (
            f"RunbookGrant(runbook={self.runbook_id}@{self.runbook_content_sha[:8]}"
            f" tool={self.tool_name} tenant={self.tenant_id}"
            f" at={self.granted_at.isoformat()})"
        )


# ---------------------------------------------------------------------------
# Rejection exceptions
# ---------------------------------------------------------------------------


class RunbookAuthorizationError(RuntimeError):
    """Base class for all runbook-scope authorization rejections."""


@attrs.define(kw_only=True, slots=False)
class ToolNotInRunbookError(RunbookAuthorizationError):
    """
    Raised when the requested tool is not in the runbook's allowed list,
    or is explicitly denied.

    :param tool_name: The tool that was requested.
    :param runbook_id: The active runbook that denied it.
    """

    tool_name: str
    runbook_id: str

    def __str__(self) -> str:
        return f"Tool '{self.tool_name}' is not authorised by runbook '{self.runbook_id}'"


@attrs.define(kw_only=True, slots=False)
class TenantScopeViolationError(RunbookAuthorizationError):
    """
    Raised when a tool's ``namespace`` argument belongs to a different tenant
    than the envelope's ``tenant_id``.

    :param tenant_id: The envelope tenant (who owns this investigation).
    :param call_namespace: The namespace the tool was asked to query.
    """

    tenant_id: str
    call_namespace: str

    def __str__(self) -> str:
        return (
            f"Cross-tenant tool call: envelope tenant='{self.tenant_id}' "
            f"but namespace='{self.call_namespace}'"
        )


@attrs.define(kw_only=True, slots=False)
class ToolBudgetExceededError(RunbookAuthorizationError):
    """
    Raised when a tool call would exceed the runbook's per-tool or total
    budget defined in ``tools.yaml``.

    :param tool_name: The tool that hit its limit.
    :param max_calls: The configured limit.
    :param scope: ``"per_tool"`` or ``"total"``.
    """

    tool_name: str
    max_calls: int
    scope: str

    def __str__(self) -> str:
        return (
            f"Budget exceeded for tool '{self.tool_name}': "
            f"{self.scope} limit of {self.max_calls} reached"
        )


# ---------------------------------------------------------------------------
# Authorization function
# ---------------------------------------------------------------------------


def authorize_tool_call(
    *,
    runbook: runbook_models.Runbook,
    tool_name: str,
    tenant_id: str,
    call_namespace: str | None = None,
    now: datetime | None = None,
) -> RunbookGrant:
    """
    Validate a tool invocation against the active runbook and return a grant.

    Checks (in order):
    1. Tool must not be in ``denied_tools``.
    2. Tool must be in ``allowed_tool_names``.
    3. If ``call_namespace`` is provided, it must match ``tenant_id``.

    :param runbook: The active runbook whose ``tools.yaml`` governs the call.
    :param tool_name: The tool being requested.
    :param tenant_id: The envelope's tenant identity.
    :param call_namespace: Optional namespace arg extracted from ``tool_args``
        by the wrapper. ``None`` means the tool is namespace-agnostic.
    :param now: Wall-clock override for the grant timestamp (test injection).
    :returns: :class:`RunbookGrant` on success.
    :raises ToolNotInRunbookError: If the tool is denied or not listed.
    :raises TenantScopeViolationError: If ``call_namespace != tenant_id``.
    """
    if tool_name in runbook.tools.denied_tools:
        raise ToolNotInRunbookError(
            tool_name=tool_name,
            runbook_id=runbook.runbook_id,
        )

    if tool_name not in runbook.tools.allowed_tool_names:
        raise ToolNotInRunbookError(
            tool_name=tool_name,
            runbook_id=runbook.runbook_id,
        )

    if call_namespace is not None and call_namespace != tenant_id:
        raise TenantScopeViolationError(
            tenant_id=tenant_id,
            call_namespace=call_namespace,
        )

    return RunbookGrant(
        runbook_id=runbook.runbook_id,
        runbook_content_sha=runbook.metadata.content_sha,
        tool_name=tool_name,
        tenant_id=tenant_id,
        granted_at=now if now is not None else datetime.now(tz=UTC),
    )
