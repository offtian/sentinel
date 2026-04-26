"""
Builder for the LangGraph ``AsyncPostgresSaver`` used as the workflow
checkpointer.

The saver owns its own ``psycopg_pool.AsyncConnectionPool`` -- separate
from the application's SQLAlchemy pool -- because LangGraph's checkpointer
speaks libpq via psycopg, and pool ownership belonging to the checkpointer
keeps the lifecycle obvious. The bootstrap layer (FastAPI lifespan)
constructs the saver once, stashes it on ``app.state``, and calls the
returned close callable on shutdown.

**Pool option (B from the Phase 2 task brief):** instantiate the saver
directly with an ``AsyncConnectionPool``; expose ``pool.close`` as the
shutdown handle. Picked over Option A (returning the
``from_conn_string`` async-context-manager exit fn) because:

- Pool tuning (``min_size``, ``max_size``, ``timeout``) lives where it
  belongs without re-implementing connection lifecycle.
- The ``AsyncPostgresSaver`` source uses ``autocommit=True`` and
  ``prepare_threshold=0`` per connection; the pool's ``configure``
  callback applies those once per connection rather than per
  ``from_conn_string`` re-entry.
- Foundation-grade explicit ownership: the function returns
  ``(saver, async_close)`` and the close handle is the pool's own
  ``close()`` -- no hidden context manager held open for the app's
  lifetime.

Public API: :func:`build_checkpointer` only. Pool internals stay private.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import psycopg
import psycopg_pool
from langgraph.checkpoint.postgres import aio as lg_postgres_aio
from langgraph.checkpoint.serde import jsonplus as lg_serde
from psycopg import rows as psycopg_rows

from sentinel.data import _dsn


# Default pool sizing tuned for foundation-stage traffic. The numbers come
# from the langgraph-checkpoint-postgres examples and are conservative
# enough that they will not starve the test database under unit-suite
# load. Revisit under production traffic.
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 4


# Type alias for the connection rows the saver expects (dict-of-string).
# ``AsyncPostgresSaver.from_conn_string`` constructs connections with
# ``row_factory=dict_row``; the typed pool below mirrors that contract.
_DictRow = dict[str, Any]


class _SettingsProtocol(Protocol):
    """Minimum surface this module reads from ``Settings``."""

    database_url: str
    langgraph_checkpoint_dsn: str | None


async def _configure_connection(conn: psycopg.AsyncConnection[_DictRow]) -> None:
    """
    Match the per-connection settings ``AsyncPostgresSaver.from_conn_string``
    applies upstream: ``autocommit=True`` and ``prepare_threshold=0``.

    Without these two flags the saver's checkpoint writes either deadlock
    on transaction state or hit prepared-statement-cache pathologies.
    """
    await conn.set_autocommit(True)
    conn.prepare_threshold = 0


def _resolve_dsn(settings: _SettingsProtocol) -> str:
    """
    Return the libpq DSN the saver should connect with.

    Prefers ``settings.langgraph_checkpoint_dsn`` when set; otherwise
    derives the libpq form of ``settings.database_url`` via the shared
    :func:`sentinel.data._dsn.to_libpq` helper.
    """
    if settings.langgraph_checkpoint_dsn:
        return settings.langgraph_checkpoint_dsn
    return _dsn.to_libpq(settings.database_url)


async def build_checkpointer(
    settings: _SettingsProtocol,
) -> tuple[lg_postgres_aio.AsyncPostgresSaver, Callable[[], Awaitable[None]]]:
    """
    Build an ``AsyncPostgresSaver`` backed by a dedicated psycopg pool.

    The returned saver is ready to use as a LangGraph checkpointer; the
    returned close callable releases the pool and MUST be awaited on app
    shutdown to avoid leaking psycopg connections.

    The function calls ``saver.setup()`` once before returning so the
    caller never has to remember to. ``setup()`` is idempotent per
    library docs (verified by the Phase 1 spike); calling
    ``build_checkpointer`` repeatedly across app lifecycles is safe.

    :param settings: Object exposing ``database_url`` and
        ``langgraph_checkpoint_dsn``. The latter wins when set; otherwise
        the SQLAlchemy ``+asyncpg`` suffix is stripped from the former.
    :returns: A ``(saver, close)`` tuple. ``close`` is an async callable
        that closes the underlying connection pool.
    """
    dsn = _resolve_dsn(settings)
    pool: psycopg_pool.AsyncConnectionPool[psycopg.AsyncConnection[_DictRow]] = (
        psycopg_pool.AsyncConnectionPool(
            conninfo=dsn,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            kwargs={"row_factory": psycopg_rows.dict_row},
            configure=_configure_connection,
            open=False,
        )
    )
    await pool.open(wait=True)
    # ``pickle_fallback=True`` lets the serde round-trip our attrs.frozen
    # primitives (Envelope, ConfidenceScore, etc.) that ride inside the
    # SupportReviewState TypedDict. ormsgpack on its own only knows
    # Pydantic v1/v2, namedtuples, and a handful of stdlib types; attrs
    # classes fall through to the pickle path. The checkpointer DB is
    # internal, written only by trusted workflow code, so the pickle
    # security trade-off is acceptable at this stage. Revisit if/when
    # the checkpointer table is exposed to a less-trusted boundary.
    serde = lg_serde.JsonPlusSerializer(pickle_fallback=True)
    saver = lg_postgres_aio.AsyncPostgresSaver(conn=pool, serde=serde)
    await saver.setup()
    return saver, pool.close
