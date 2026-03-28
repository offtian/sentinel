from __future__ import annotations

import fastapi

from sentinel.settings import get_settings


def require_database() -> None:
    """FastAPI dependency that returns 503 when the database is not configured."""
    if not get_settings().database_url:
        raise fastapi.HTTPException(
            status_code=503,
            detail="Database not configured",
        )
