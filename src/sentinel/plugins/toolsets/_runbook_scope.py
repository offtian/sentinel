"""
Runbook-scoped toolset wrapper (F7).

:class:`RunbookScopedToolset` intercepts every tool invocation from a
PydanticAI agent, validates it against the active :class:`Runbook`'s
``tools.yaml`` allow-list, enforces per-tool and total call budgets, then
delegates to the underlying toolset.

Enforcement is at the **toolset wrapper boundary** — not at function entry —
because function-entry checks are bypassable by indirect prompt injection
routes that re-enter the toolset (Cerbos / OWASP / SuperTokens guidance;
F6 contract update §F7.2).

Layering: ``wrap_for_replay(wrap_for_runbook_scope(toolset))`` — replay
captures the rejection as ``<error: ...>`` before re-raising, preserving
replay-bundle determinism (F4.7 / F7 plan §Wrapper layering).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace as otel_trace
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from sentinel.domain.tools import grants as grants_mod
from sentinel.utils import logs


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------

_AuditFn = Callable[..., Awaitable[Any]]


class RunbookScopedToolset(WrapperToolset[Any]):
    """
    Toolset wrapper that enforces runbook-scoped tool authorization.

    On every :meth:`call_tool`:

    1. Reads the active ``runbook`` and ``envelope`` from
       :attr:`~pydantic_ai.tools.RunContext.deps`.
    2. If no runbook is bound (``runbook is None``), passes through
       (foundations soft-fail — many paths run without a matched runbook).
    3. Calls :func:`~sentinel.domain.tools.grants.authorize_tool_call`.
    4. Checks per-tool and total call budgets from ``runbook.tools``.
    5. Delegates to the wrapped toolset on success; increments the run-scoped
       counter in ``deps._tool_call_counters``.

    Rejections emit a ``tool_grant_denied`` structured log event and, when an
    ``audit_fn`` is supplied, an ``audit_log`` row via the injected callable.
    """

    def __init__(
        self,
        wrapped: AbstractToolset[Any],
        *,
        label: str | None = None,
        audit_fn: _AuditFn | None = None,
    ) -> None:
        super().__init__(wrapped)
        self._label = label or wrapped.__class__.__name__
        self._audit_fn = audit_fn

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        """Authorize and delegate the tool call; reject with audit on violation."""
        deps = ctx.deps
        runbook = getattr(deps, "runbook", None)
        envelope = getattr(deps, "envelope", None)
        counters: dict[str, int] = getattr(deps, "_tool_call_counters", {})

        if runbook is None:
            logs.log_event(
                "runbook_scope_check_skipped",
                params={"tool": name, "label": self._label},
            )
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)

        call_namespace: str | None = tool_args.get("namespace")
        tenant_id: str = envelope.tenant_id if envelope is not None else ""
        called_at = datetime.now(tz=UTC)

        # --- Authorization ---
        try:
            grant = grants_mod.authorize_tool_call(
                runbook=runbook,
                tool_name=name,
                tenant_id=tenant_id,
                call_namespace=call_namespace,
                now=called_at,
            )
        except grants_mod.RunbookAuthorizationError as exc:
            await self._handle_rejection(
                exc=exc,
                tool_name=name,
                tool_args=tool_args,
                runbook_id=runbook.runbook_id,
                runbook_content_sha=runbook.metadata.content_sha,
                tenant_id=tenant_id,
                call_namespace=call_namespace,
                called_at=called_at,
            )
            raise

        # --- Budget enforcement ---
        per_tool_count = counters.get(name, 0)
        per_tool_max = runbook.tools.tool_max_calls.get(name)
        if per_tool_max is not None and per_tool_count >= per_tool_max:
            budget_exc = grants_mod.ToolBudgetExceededError(
                tool_name=name,
                max_calls=per_tool_max,
                scope="per_tool",
            )
            await self._handle_rejection(
                exc=budget_exc,
                tool_name=name,
                tool_args=tool_args,
                runbook_id=runbook.runbook_id,
                runbook_content_sha=runbook.metadata.content_sha,
                tenant_id=tenant_id,
                call_namespace=call_namespace,
                called_at=called_at,
            )
            raise budget_exc

        total_count = sum(counters.values())
        if total_count >= runbook.tools.max_total_tool_calls:
            budget_exc = grants_mod.ToolBudgetExceededError(
                tool_name=name,
                max_calls=runbook.tools.max_total_tool_calls,
                scope="total",
            )
            await self._handle_rejection(
                exc=budget_exc,
                tool_name=name,
                tool_args=tool_args,
                runbook_id=runbook.runbook_id,
                runbook_content_sha=runbook.metadata.content_sha,
                tenant_id=tenant_id,
                call_namespace=call_namespace,
                called_at=called_at,
            )
            raise budget_exc

        # --- Increment counter before delegation (counts authorized attempts) ---
        counters[name] = per_tool_count + 1

        # --- OTel span attributes ---
        otel_trace.get_current_span().set_attributes(
            {
                "runbook.grant.runbook_id": grant.runbook_id,
                "runbook.grant.tool_name": grant.tool_name,
                "runbook.grant.tenant_id": grant.tenant_id,
            }
        )

        return await self.wrapped.call_tool(name, tool_args, ctx, tool)

    async def _handle_rejection(
        self,
        *,
        exc: grants_mod.RunbookAuthorizationError,
        tool_name: str,
        tool_args: dict[str, Any],
        runbook_id: str,
        runbook_content_sha: str,
        tenant_id: str,
        call_namespace: str | None,
        called_at: datetime,
    ) -> None:
        rejection_kind = type(exc).__name__
        logs.log_event(
            "tool_grant_denied",
            params={
                "rejection_kind": rejection_kind,
                "tool_name": tool_name,
                "runbook_id": runbook_id,
                "tenant_id": tenant_id,
                "call_namespace": call_namespace,
                "label": self._label,
            },
        )
        if self._audit_fn is not None:
            input_hash = hashlib.sha256(
                json.dumps(tool_args, sort_keys=True, default=str).encode()
            ).hexdigest()
            await self._audit_fn(
                actor="tool_runtime",
                action="tool_call_rejected",
                resource_type="tool",
                resource_id=tool_name,
                details={
                    "rejection_kind": rejection_kind,
                    "runbook_id": runbook_id,
                    "runbook_content_sha": runbook_content_sha,
                    "tenant_id": tenant_id,
                    "call_namespace": call_namespace,
                    "attempted_at": called_at.isoformat(),
                },
                input_hash=input_hash,
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def wrap_for_runbook_scope(
    toolset: AbstractToolset[Any] | None,
    *,
    label: str | None = None,
    audit_fn: _AuditFn | None = None,
) -> AbstractToolset[Any] | None:
    """
    Wrap *toolset* with :class:`RunbookScopedToolset` for authorization.

    Returns ``None`` unchanged so callers can pipe optional toolsets without
    conditional branching. Already-wrapped toolsets are returned as-is
    (idempotent). Mirrors :func:`~sentinel.plugins.toolsets._runtime.wrap_for_replay`.

    :param toolset: The toolset to wrap, or ``None``.
    :param label: Human-readable label for log lines.
    :param audit_fn: Optional async callable for writing audit_log rows on
        rejection. Signature: ``audit_fn(*, actor, action, resource_type,
        resource_id, details, input_hash) -> Any``. Typically a
        ``functools.partial`` of :func:`~sentinel.domain.audit.operations.record_audit_entry`
        with the ``db`` argument pre-bound.
    """
    if toolset is None:
        return None
    if isinstance(toolset, RunbookScopedToolset):
        return toolset
    logs.log_event(
        "toolset_runbook_scope_attached",
        params={"label": label or toolset.__class__.__name__},
    )
    return RunbookScopedToolset(toolset, label=label, audit_fn=audit_fn)
