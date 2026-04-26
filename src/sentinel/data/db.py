"""
Async database singleton using the databases library.

Provides a single databases.Database instance shared across the application.
All entry points (API, worker, MCP server) call connect_db() on startup
and disconnect_db() on shutdown.
"""

from __future__ import annotations

import databases

from sentinel.data import _dsn
from sentinel.settings import get_settings


_db: databases.Database | None = None


def get_db() -> databases.Database:
    """
    Return the cached databases.Database singleton.

    :raises RuntimeError: if DATABASE_URL is not configured.
    """
    global _db  # noqa: PLW0603
    if _db is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Set the DATABASE_URL environment variable."
            )
        # The databases library expects libpq URLs, not the SQLAlchemy +asyncpg form.
        _db = databases.Database(_dsn.to_libpq(url))
    return _db


async def connect_db() -> None:
    """Open the database connection pool. Call during application startup."""
    db = get_db()
    await db.connect()


async def disconnect_db() -> None:
    """Close the database connection pool and reset the singleton."""
    global _db  # noqa: PLW0603
    if _db is not None:
        await _db.disconnect()
        _db = None
