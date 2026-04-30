"""
Shared fixtures for integration tests.

All integration tests in this tree require a running Postgres database.
The ``skip_without_db`` fixture performs a quick connectivity check and skips
the test (rather than failing with a 30-second pool timeout) when the DB is
not reachable.
"""

from __future__ import annotations

import os
import socket

import pytest


def _db_is_reachable() -> bool:
    """Return True when the configured DB host/port accepts a TCP connection."""
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return False
    host = "localhost"
    port = 5432
    try:
        after_at = raw_url.rsplit("@", 1)[-1]
        host_port = after_at.split("/")[0]
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
    except (ValueError, IndexError):
        pass
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def db_available() -> bool:
    """Session-scoped flag: True when Postgres is reachable."""
    return _db_is_reachable()


@pytest.fixture(autouse=True)
def skip_without_db(request: pytest.FixtureRequest, db_available: bool) -> None:  # noqa: FBT001
    """Auto-skip any integration test when the database is not reachable."""
    if not db_available:
        pytest.skip("No database reachable — run `just docker-compose-up` first")
