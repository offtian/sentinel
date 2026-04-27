"""
Unit tests for the F6.J.4 application-layer runbook reindex daemon hook.

The reindex function walks the discovered runbook catalog and calls
``rag.index_runbook`` per runbook, idempotent on the unique
``(runbook_id, content_sha, section, model, model_ver)`` key. The
application layer owns the orchestration: deciding *whether* to walk
(driven by ``BaseConfiguration.enable_rag_fallback``), counting outcomes,
and surfacing a small ``ReindexReport`` for callers.

Async I/O against a live pgvector container is a follow-up — covered by
F6.J.6's deferred integration test, not this unit-level surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from unittest import mock

import attrs
import pytest

from sentinel.application.runbooks import _indexing
from sentinel.domain.runbooks import models, rag


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_runbook(*, runbook_id: str, content_sha: str = "0" * 32) -> models.Runbook:
    """Build a minimal :class:`models.Runbook` for the reindex walker tests."""
    metadata = models.RunbookMetadata(
        runbook_id=runbook_id,
        description=f"Procedure for {runbook_id}.",
        content_sha=content_sha,
        applies_to=models.RunbookAppliesTo(
            alertnames=(),
            severity_min="P3",
            resource_kinds=("Pod",),
            exclude_labels={},
        ),
        tags=(),
        min_match_score=0,
        owner="sre-platform",
        authors=("ollie.tian",),
        last_validated=date(2026, 4, 26),
        deprecated_at=None,
        superseded_by=None,
        mnpi_safe=True,
        canonical_sources=(),
    )
    return models.Runbook(
        metadata=metadata,
        body="placeholder body",
        tools=models.ToolsConfig(
            allowed_tools=(),
            denied_tools=(),
            max_total_tool_calls=10,
            max_loop_iterations=4,
        ),
        checks=models.ChecksConfig(
            prescribed_checks=(),
            groundedness_rules=(),
            body_sanitization=models.BodySanitizationConfig(
                reject_auto_rendered_urls=False,
                allowed_url_locations=(),
            ),
        ),
        tests=(),
        directory=Path("/tmp/runbooks") / runbook_id,  # noqa: S108
    )


@attrs.frozen(kw_only=True, slots=True)
class _StubEmbedder:
    """Minimal :class:`rag.Embedder` test double. Never called by these tests."""

    model_id: str = "stub/embed-test"
    model_version: str = "v1"

    async def embed(self, text: str) -> tuple[float, ...]:
        # Return a fixed-shape vector — exact values are irrelevant because
        # the reindex tests mock ``rag.index_runbook`` outright.
        return (0.0,) * 4


def _catalog(*runbooks: models.Runbook) -> Mapping[str, models.Runbook]:
    return {runbook.metadata.runbook_id: runbook for runbook in runbooks}


# ---------------------------------------------------------------------------
# ReindexReport
# ---------------------------------------------------------------------------


class TestReindexReport:
    def test_default_construction_zeroes_every_counter(self) -> None:
        # Given the public default constructor
        # When called with no arguments
        report = _indexing.ReindexReport(indexed=0, skipped=0, failed=0)

        # Then every counter is zero so the report is safe to publish
        # immediately — empty catalog, RAG disabled, etc.
        assert report.indexed == 0
        assert report.skipped == 0
        assert report.failed == 0

    def test_is_immutable(self) -> None:
        # Given a constructed report
        report = _indexing.ReindexReport(indexed=1, skipped=2, failed=3)

        # When a field is reassigned
        # Then attrs raises FrozenInstanceError because the class is frozen
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            report.indexed = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# reindex_all_runbooks — happy path
# ---------------------------------------------------------------------------


class TestReindexAllRunbooks:
    async def test_indexes_every_discovered_runbook(self, monkeypatch) -> None:
        # Given two runbooks discovered by the loader
        crashloop = _make_runbook(runbook_id="k8s-crashloop")
        latency = _make_runbook(runbook_id="latency-spike")
        catalog = _catalog(crashloop, latency)

        monkeypatch.setattr(
            _indexing.loader,
            "discover_runbooks",
            mock.Mock(return_value=catalog),
        )
        index_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(_indexing.rag, "index_runbook", index_mock)

        embedder = _StubEmbedder()
        session = mock.AsyncMock()

        # When the daemon walks every discovered runbook
        report = await _indexing.reindex_all_runbooks(
            session=session,
            embedder=embedder,
            runbooks_paths=(Path("/tmp/runbooks-root"),),  # noqa: S108
            enable_rag_fallback=True,
        )

        # Then index_runbook is called once per runbook with the matching kwargs
        assert index_mock.await_count == 2
        called_runbook_ids = {
            call.kwargs["runbook"].metadata.runbook_id for call in index_mock.await_args_list
        }
        assert called_runbook_ids == {"k8s-crashloop", "latency-spike"}
        for call in index_mock.await_args_list:
            assert call.kwargs["session"] is session
            assert call.kwargs["embedder"] is embedder

        # And the report counts every successful index
        assert report.indexed == 2
        assert report.skipped == 0
        assert report.failed == 0

    async def test_returns_zero_counts_for_empty_catalog(self, monkeypatch) -> None:
        # Given a discovery walk that returns an empty mapping (no runbooks on disk)
        monkeypatch.setattr(
            _indexing.loader,
            "discover_runbooks",
            mock.Mock(return_value={}),
        )
        index_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(_indexing.rag, "index_runbook", index_mock)

        # When the daemon walks
        report = await _indexing.reindex_all_runbooks(
            session=mock.AsyncMock(),
            embedder=_StubEmbedder(),
            runbooks_paths=(),
            enable_rag_fallback=True,
        )

        # Then index_runbook is never called and the report reports zeros
        index_mock.assert_not_awaited()
        assert report == _indexing.ReindexReport(indexed=0, skipped=0, failed=0)


# ---------------------------------------------------------------------------
# reindex_all_runbooks — disabled toggle
# ---------------------------------------------------------------------------


class TestReindexAllRunbooksWhenDisabled:
    async def test_skips_walk_when_rag_fallback_disabled(self, monkeypatch) -> None:
        # Given enable_rag_fallback is False and the loader would otherwise
        # return runbooks
        crashloop = _make_runbook(runbook_id="k8s-crashloop")
        discover_mock = mock.Mock(return_value=_catalog(crashloop))
        monkeypatch.setattr(_indexing.loader, "discover_runbooks", discover_mock)
        index_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(_indexing.rag, "index_runbook", index_mock)

        # When the daemon is invoked with the toggle off
        report = await _indexing.reindex_all_runbooks(
            session=mock.AsyncMock(),
            embedder=_StubEmbedder(),
            runbooks_paths=(Path("/tmp/runbooks-root"),),  # noqa: S108
            enable_rag_fallback=False,
        )

        # Then the loader walk is short-circuited entirely — no I/O, no
        # index_runbook calls, and the report is all-zero so callers can log
        # it uniformly with the enabled path
        discover_mock.assert_not_called()
        index_mock.assert_not_awaited()
        assert report == _indexing.ReindexReport(indexed=0, skipped=0, failed=0)


# ---------------------------------------------------------------------------
# reindex_all_runbooks — failure handling
# ---------------------------------------------------------------------------


class TestReindexAllRunbooksFailureHandling:
    async def test_continues_walking_when_one_runbook_fails(self, monkeypatch) -> None:
        # Given two runbooks where the first raises EmbedderUnavailableError
        crashloop = _make_runbook(runbook_id="k8s-crashloop")
        latency = _make_runbook(runbook_id="latency-spike")
        catalog = _catalog(crashloop, latency)

        monkeypatch.setattr(
            _indexing.loader,
            "discover_runbooks",
            mock.Mock(return_value=catalog),
        )

        async def _flaky_index(*, runbook, session, embedder):
            if runbook.metadata.runbook_id == "k8s-crashloop":
                raise rag.EmbedderUnavailableError("transport down")

        monkeypatch.setattr(_indexing.rag, "index_runbook", _flaky_index)

        # When the daemon walks
        report = await _indexing.reindex_all_runbooks(
            session=mock.AsyncMock(),
            embedder=_StubEmbedder(),
            runbooks_paths=(Path("/tmp/runbooks-root"),),  # noqa: S108
            enable_rag_fallback=True,
        )

        # Then the failing runbook is recorded as failed and the second is
        # still indexed — one bad embedder hit shouldn't break the catalog walk
        assert report.indexed == 1
        assert report.failed == 1
        assert report.skipped == 0


# ---------------------------------------------------------------------------
# register_runbook_reindex_startup — callback wiring
# ---------------------------------------------------------------------------


class TestRegisterRunbookReindexStartup:
    def test_returns_a_coroutine_that_invokes_reindex(self) -> None:
        # Given the helper that builds an awaitable startup callback
        # When called with the dependencies
        callback = _indexing.register_runbook_reindex_startup(
            session_factory=mock.MagicMock(),
            embedder=_StubEmbedder(),
            runbooks_paths=(Path("/tmp/runbooks-root"),),  # noqa: S108
            enable_rag_fallback=True,
        )

        # Then a callable awaiting the reindex was returned for the caller
        # (FastAPI startup, application bootstrap, etc.) to schedule
        assert callable(callback)


# TODO(F6.J.6 follow-up): integration test against a live pgvector container —
# currently deferred because asyncpg + the pgvector extension require a
# running Postgres which we don't spin up in unit tests.
