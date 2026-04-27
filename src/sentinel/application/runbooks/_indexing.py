"""
F6.J.4 — Application-layer runbook reindex daemon hook.

Walks the discovered runbook catalog and calls :func:`rag.index_runbook`
per runbook. Idempotent: ``rag.index_runbook`` upserts on the unique
``(runbook_id, content_sha, section, model, model_ver)`` key, so repeat
runs are no-ops on unchanged content.

The application layer owns three concerns this module exposes:

1. Whether to walk at all — driven by the
   :attr:`sentinel.config.BaseConfiguration.enable_rag_fallback` flag the
   caller threads in. When disabled, the catalog walk is short-circuited
   so a deployment without RAG infrastructure never pays the discovery
   I/O cost.
2. Outcome accounting — :class:`ReindexReport` aggregates indexed /
   skipped / failed counts so callers can log a single line per startup
   pass and surface a degraded warning when ``failed > 0``.
3. Per-runbook failure isolation — one bad embedder call (transport
   down, model deprecated, runbook content too long) should never abort
   the catalog walk; the report records the failure and processing
   continues.

The startup wiring helper :func:`register_runbook_reindex_startup` returns
a zero-argument coroutine factory the FastAPI app or application bootstrap
can schedule on startup. It does not call FastAPI itself — that wiring lives
with the caller so this module remains FastAPI-agnostic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

import attrs
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.domain.runbooks import loader, rag
from sentinel.utils import logs


@attrs.frozen(kw_only=True, slots=True)
class ReindexReport:
    """
    Aggregate outcome of a single :func:`reindex_all_runbooks` walk.

    ``indexed`` counts runbooks where :func:`rag.index_runbook` returned
    successfully (which itself is a no-op for already-indexed sections,
    so the count blends fresh writes and steady-state passes).
    ``skipped`` is reserved for future application-layer skip logic
    (deprecated runbooks, etc.) — currently always zero. ``failed``
    counts runbooks where the embedder raised; the catalog walk
    continues past failures so one outage doesn't break every later
    runbook in the same pass.
    """

    indexed: int
    skipped: int
    failed: int


class _SessionFactory(Protocol):
    """Callable returning a fresh :class:`AsyncSession` per startup invocation."""

    def __call__(self) -> AsyncSession: ...


async def reindex_all_runbooks(
    *,
    session: AsyncSession,
    embedder: rag.Embedder,
    runbooks_paths: Sequence[Path],
    enable_rag_fallback: bool,
) -> ReindexReport:
    """
    Walk every runbook discovered under ``runbooks_paths`` and index it.

    Idempotent on the F6.J.3 unique key — re-running on unchanged content
    is a no-op. One transaction per call: the caller commits or rolls back
    after the walk; this function only flushes per-runbook via
    :func:`rag.index_runbook`.

    :param session: Open :class:`AsyncSession` the upsert path will use.
    :param embedder: The :class:`rag.Embedder` to feed each runbook section
        into. The application layer pins the model + model-version on the
        embedder; this function does not consult ``Settings`` directly.
    :param runbooks_paths: Discovery roots fed straight to
        :func:`loader.discover_runbooks`. Empty tuple = empty catalog.
    :param enable_rag_fallback: When False, the walk is short-circuited
        entirely — no discovery, no I/O, no log noise. Caller threads this
        in from :attr:`sentinel.config.BaseConfiguration.enable_rag_fallback`
        so the application layer never imports config above it.
    :returns: A :class:`ReindexReport` with per-outcome counters.
    """
    if not enable_rag_fallback:
        logs.log_event(
            "runbook_reindex_skipped_rag_disabled",
            params={"runbooks_paths": [str(path) for path in runbooks_paths]},
        )
        return ReindexReport(indexed=0, skipped=0, failed=0)

    catalog = loader.discover_runbooks(runbooks_paths)
    indexed = 0
    skipped = 0
    failed = 0
    for runbook in catalog.values():
        try:
            await rag.index_runbook(session=session, runbook=runbook, embedder=embedder)
        except rag.EmbedderUnavailableError as exc:
            failed += 1
            logs.log_exception(
                exc,
                params={
                    "runbook_id": runbook.metadata.runbook_id,
                    "content_sha": runbook.metadata.content_sha,
                    "model_id": embedder.model_id,
                    "model_version": embedder.model_version,
                },
            )
            continue
        indexed += 1
    logs.log_event(
        "runbook_reindex_complete",
        params={
            "indexed": indexed,
            "skipped": skipped,
            "failed": failed,
            "runbooks_paths": [str(path) for path in runbooks_paths],
        },
    )
    return ReindexReport(indexed=indexed, skipped=skipped, failed=failed)


def register_runbook_reindex_startup(
    *,
    session_factory: _SessionFactory,
    embedder: rag.Embedder,
    runbooks_paths: Sequence[Path],
    enable_rag_fallback: bool,
) -> Callable[[], Awaitable[ReindexReport]]:
    """
    Return a zero-argument coroutine factory the caller can schedule on startup.

    Built so the FastAPI app or application bootstrap can register the walk
    without this module importing from :mod:`fastapi` (the application layer
    cannot reach into :mod:`interfaces`). The session is allocated *inside*
    the returned coroutine so the session lifetime is bounded to the walk;
    the caller injects a ``session_factory`` that already knows how to build
    one against the live database.

    :param session_factory: Zero-argument callable returning a fresh
        :class:`AsyncSession`. Called once per startup invocation.
    :param embedder: The :class:`rag.Embedder` to use for the walk.
    :param runbooks_paths: Discovery roots fed to
        :func:`loader.discover_runbooks` inside the returned callback.
    :param enable_rag_fallback: When False, the returned callback is a
        no-op — see :func:`reindex_all_runbooks`.
    :returns: A zero-arg async callable returning a :class:`ReindexReport`.
    """

    async def _startup_callback() -> ReindexReport:
        session = session_factory()
        try:
            return await reindex_all_runbooks(
                session=session,
                embedder=embedder,
                runbooks_paths=runbooks_paths,
                enable_rag_fallback=enable_rag_fallback,
            )
        finally:
            await session.close()

    return _startup_callback


__all__ = (
    "ReindexReport",
    "register_runbook_reindex_startup",
    "reindex_all_runbooks",
)
