"""
DSN translation helpers for the data layer.

The application's ``DATABASE_URL`` is a SQLAlchemy-flavoured URL such as
``postgresql+asyncpg://...`` because SQLAlchemy chooses the driver via that
suffix. Other libraries (notably the ``databases`` library and the
``langgraph-checkpoint-postgres`` saver via psycopg) speak plain libpq URLs
and reject the driver suffix. The strip is the single textual difference
between the two forms; both URLs point at the same database.

This module exists so the strip lives in one place. Importers ought to call
``to_libpq()`` rather than re-implementing the replace inline.
"""

from __future__ import annotations


_ASYNCPG_DRIVER_SUFFIX = "+asyncpg"


def to_libpq(url: str) -> str:
    """
    Return the libpq form of a SQLAlchemy-flavoured Postgres URL.

    Strips the ``+asyncpg`` driver suffix from the scheme so libraries that
    expect a plain libpq URL (psycopg, the ``databases`` library) accept the
    string. URLs that already lack the suffix pass through unchanged.

    :param url: The SQLAlchemy-flavoured URL, e.g.
        ``postgresql+asyncpg://user@host/db``.
    """
    return url.replace(_ASYNCPG_DRIVER_SUFFIX, "")
