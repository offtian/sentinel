"""
Integration tests for ``build_checkpointer`` against the real Postgres
test database.

Phase 2 / T6 of the LangGraph adoption plan. The function returns a
ready-to-use ``AsyncPostgresSaver`` (with its own connection pool) plus
an async close callable that the FastAPI lifespan calls on shutdown.

Pre-requisites:
- Local Postgres reachable at ``DATABASE_URL`` from ``pyproject.toml``'s
  ``pytest.ini_options.env`` block.
- The saver is given the libpq-flavoured URL via
  ``sentinel.data._dsn.to_libpq``; the same convention applies whether
  ``LANGGRAPH_CHECKPOINT_DSN`` is set explicitly or it falls back to
  ``DATABASE_URL``.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from typing import TypedDict

import psycopg_pool
import pytest
from langgraph import graph as lg_graph
from langgraph.checkpoint.postgres import aio as lg_postgres_aio

from sentinel.data import _dsn
from sentinel.interfaces.workflows import _checkpointer as workflows_checkpointer


@dataclasses.dataclass
class _StubSettings:
    """Minimal duck-typed settings the checkpointer reads from."""

    database_url: str = ""
    langgraph_checkpoint_dsn: str | None = None


def _libpq_dsn_from_env() -> str:
    return _dsn.to_libpq(os.environ["DATABASE_URL"])


class _PingState(TypedDict, total=False):
    """Trivial state for the round-trip checkpoint assertion."""

    seen: int


async def _ping_node(state: _PingState) -> dict:
    """Single-node body: writes a deterministic value the test can read back."""
    return {"seen": state.get("seen", 0) + 1}


def _build_ping_graph(saver: lg_postgres_aio.AsyncPostgresSaver):
    """Compile a single-node graph using the supplied saver as checkpointer."""
    builder: lg_graph.StateGraph = lg_graph.StateGraph(_PingState)
    builder.add_node("ping", _ping_node)
    builder.add_edge(lg_graph.START, "ping")
    builder.add_edge("ping", lg_graph.END)
    return builder.compile(checkpointer=saver)


class TestBuildCheckpointer:
    @pytest.mark.asyncio
    async def test_returns_saver_with_setup_idempotent(self) -> None:
        # Given a settings stub that points at the test database via DATABASE_URL fallback
        settings = _StubSettings(
            database_url=os.environ["DATABASE_URL"],
            langgraph_checkpoint_dsn=None,
        )

        # When build_checkpointer is called twice in sequence
        first_saver, first_close = await workflows_checkpointer.build_checkpointer(settings)
        try:
            second_saver, second_close = await workflows_checkpointer.build_checkpointer(settings)
            try:
                # Then both calls return a saver and the second setup() did not raise
                # (idempotent — the checkpoint tables already exist after the first call)
                assert isinstance(first_saver, lg_postgres_aio.AsyncPostgresSaver)
                assert isinstance(second_saver, lg_postgres_aio.AsyncPostgresSaver)
                # AND a checkpoint round-trip on the second saver still succeeds, proving
                # the tables are present and queryable
                graph = _build_ping_graph(second_saver)
                thread_id = f"build-checkpointer-idempotent-{uuid.uuid4()}"
                config = {"configurable": {"thread_id": thread_id}}
                result = await graph.ainvoke({"seen": 0}, config=config)
                assert result.get("seen") == 1
            finally:
                await second_close()
        finally:
            await first_close()

    @pytest.mark.asyncio
    async def test_uses_explicit_langgraph_dsn_when_set(self) -> None:
        # Given a settings stub with the explicit LangGraph DSN populated and
        # a deliberately-broken database_url that the function MUST NOT fall back to
        explicit_dsn = _libpq_dsn_from_env()
        settings = _StubSettings(
            database_url="postgresql+asyncpg://broken-host-do-not-resolve/nope",
            langgraph_checkpoint_dsn=explicit_dsn,
        )

        # When build_checkpointer connects with the explicit DSN
        saver, close = await workflows_checkpointer.build_checkpointer(settings)
        try:
            # Then the saver is usable: a trivial graph round-trips a checkpoint
            graph = _build_ping_graph(saver)
            thread_id = f"build-checkpointer-explicit-{uuid.uuid4()}"
            config = {"configurable": {"thread_id": thread_id}}
            result = await graph.ainvoke({"seen": 0}, config=config)
            assert result.get("seen") == 1
        finally:
            await close()

    @pytest.mark.asyncio
    async def test_falls_back_to_database_url_when_unset(self) -> None:
        # Given a settings stub with langgraph_checkpoint_dsn unset
        settings = _StubSettings(
            database_url=os.environ["DATABASE_URL"],
            langgraph_checkpoint_dsn=None,
        )

        # When build_checkpointer derives the DSN from database_url
        saver, close = await workflows_checkpointer.build_checkpointer(settings)
        try:
            # Then the saver works against the same test database
            graph = _build_ping_graph(saver)
            thread_id = f"build-checkpointer-fallback-{uuid.uuid4()}"
            config = {"configurable": {"thread_id": thread_id}}
            result = await graph.ainvoke({"seen": 5}, config=config)
            assert result.get("seen") == 6
        finally:
            await close()

    @pytest.mark.asyncio
    async def test_close_releases_underlying_resources(self) -> None:
        # Given a built checkpointer
        settings = _StubSettings(
            database_url=os.environ["DATABASE_URL"],
            langgraph_checkpoint_dsn=None,
        )
        saver, close = await workflows_checkpointer.build_checkpointer(settings)

        # When the close callable is awaited
        await close()

        # Then the underlying psycopg connection pool is closed: a subsequent
        # checkpoint write raises PoolClosed (or its async equivalent), proving
        # resources were released rather than leaked
        graph = _build_ping_graph(saver)
        thread_id = f"build-checkpointer-closed-{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        with pytest.raises(psycopg_pool.PoolClosed):
            await graph.ainvoke({"seen": 0}, config=config)
