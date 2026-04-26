"""
Phase 1 / T1 spike — verify ``AsyncPostgresSaver`` + ``interrupt()`` +
``Command(resume=...)`` round-trip against the real Postgres test database.

This test exists to de-risk the Phase 2 checkpointer wiring before the real
support workflow lands. The risk it covers is called out in the
``pydanticai-langgraph-adoption`` design spec under "Risks & open questions":
``Command(resume=...)`` semantics in ``langgraph-checkpoint-postgres`` 3.x are
the thing to validate before writing the real graph.

If this spike fails to round-trip, Phase 2's checkpointer code does not land
as designed.

Pre-requisites:
- Local Postgres reachable at the URL set in ``pytest.ini_options.env``
  (``DATABASE_URL=postgresql+asyncpg://postgres@localhost/sentinel-test``).
- The saver is given the libpq-flavoured URL (``+asyncpg`` stripped), matching
  the bootstrap convention for ``LANGGRAPH_CHECKPOINT_DSN``.
"""

from __future__ import annotations

import os
import uuid
from typing import TypedDict

from langgraph import graph as lg_graph
from langgraph import types as lg_types
from langgraph.checkpoint.postgres import aio as lg_postgres_aio


def _libpq_dsn_from_settings() -> str:
    """
    Return the libpq-flavoured DSN for the integration test database.

    ``langgraph-checkpoint-postgres`` uses psycopg under the hood, which
    speaks plain libpq URLs. The pytest env sets a SQLAlchemy-flavoured URL
    (``postgresql+asyncpg://...``) so the application can use asyncpg via
    SQLAlchemy. The saver needs the ``+asyncpg`` driver suffix stripped.
    """
    url = os.environ["DATABASE_URL"]
    return url.replace("+asyncpg", "")


class _SpikeState(TypedDict, total=False):
    """Minimal state for the spike — two slots, two nodes."""

    x: int
    y: int


async def _node_a(state: _SpikeState) -> dict:
    """Pre-interrupt node — returns a deterministic value."""
    return {"x": 1}


async def _node_b(state: _SpikeState) -> dict:
    """Interrupt node — pauses awaiting a resume payload, then returns it."""
    resume_payload = lg_types.interrupt({"reason": "test"})
    # ``interrupt`` raises ``GraphInterrupt`` on first invocation. On resume
    # the entire node re-executes; ``interrupt`` then returns the
    # ``Command(resume=...)`` payload.
    return {"y": resume_payload["resume_value"]}


def _build_spike_graph(saver: lg_postgres_aio.AsyncPostgresSaver):
    """Compile a 2-node graph: ``node_a`` → ``node_b`` → END."""
    builder: lg_graph.StateGraph = lg_graph.StateGraph(_SpikeState)
    builder.add_node("node_a", _node_a)
    builder.add_node("node_b", _node_b)
    builder.add_edge(lg_graph.START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", lg_graph.END)
    return builder.compile(checkpointer=saver)


class TestAsyncPostgresSaverRoundTrip:
    """Spike: AsyncPostgresSaver pauses at interrupt() and resumes on Command(resume=...)."""

    async def test_graph_pauses_at_interrupt_then_resumes_with_command(self) -> None:
        # GIVEN a real Postgres-backed AsyncPostgresSaver and a 2-node graph where
        # the second node calls ``interrupt({"reason": "test"})``. A fresh
        # thread_id per run is used because the checkpointer persists across
        # test sessions — re-using a thread_id leaks the previous run's resume
        # state into the assertions.
        dsn = _libpq_dsn_from_settings()
        thread_id = f"spike-roundtrip-{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}

        async with lg_postgres_aio.AsyncPostgresSaver.from_conn_string(dsn) as saver:
            await saver.setup()
            graph = _build_spike_graph(saver)

            # WHEN ainvoke is called with no resume payload
            paused = await graph.ainvoke({}, config=config)

            # THEN the graph pauses at node_b, surfacing the interrupt payload
            assert "__interrupt__" in paused, (
                f"expected __interrupt__ key in paused state, got keys: {list(paused.keys())}"
            )
            interrupts = paused["__interrupt__"]
            assert len(interrupts) == 1
            assert interrupts[0].value == {"reason": "test"}
            # AND node_a's write landed before the pause
            assert paused.get("x") == 1
            # AND node_b's write did not — we never reached the return
            assert "y" not in paused

            # WHEN ainvoke is called again with Command(resume=...)
            resumed = await graph.ainvoke(
                lg_types.Command(resume={"resume_value": 42}),
                config=config,
            )

            # THEN the graph resumes node_b, runs to END, and surfaces y=42
            assert "__interrupt__" not in resumed
            assert resumed.get("x") == 1
            assert resumed.get("y") == 42

    async def test_setup_is_idempotent_across_calls(self) -> None:
        # GIVEN a Postgres-backed saver instantiated from a connection string
        dsn = _libpq_dsn_from_settings()

        async with lg_postgres_aio.AsyncPostgresSaver.from_conn_string(dsn) as saver:
            # WHEN setup() is called twice in a row
            await saver.setup()

            # THEN the second call does not raise — it is a no-op when the
            # checkpoint tables already exist (per library docs).
            await saver.setup()
