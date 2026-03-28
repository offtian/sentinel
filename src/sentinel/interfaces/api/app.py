from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import fastapi

from sentinel import bootstrap
from sentinel.data import database
from sentinel.interfaces.api.routers.jobs.router import router as jobs_router
from sentinel.interfaces.api.routers.sre.router import router as sre_router
from sentinel.interfaces.api.routers.support.router import router as support_router
from sentinel.settings import get_settings
from sentinel.utils import logs


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncGenerator[None]:
    bootstrap.initialise()

    # Initialise the database engine on startup (if configured)
    if get_settings().database_url:
        database.get_engine()
        logs.log_event("database_engine_initialised")

    yield

    # Shutdown: close the database engine
    await database.close_engine()
    logs.log_event("database_engine_closed")


app = fastapi.FastAPI(
    title="Sentinel",
    description="AI SRE & AI Support Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(sre_router, prefix="/api")
app.include_router(support_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "sentinel"}


@app.get("/", include_in_schema=False)
async def root() -> fastapi.responses.RedirectResponse:
    return fastapi.responses.RedirectResponse(url="/docs")
