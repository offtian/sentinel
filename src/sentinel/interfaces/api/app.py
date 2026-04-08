from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import fastapi
from prometheus_client import make_asgi_app

from sentinel import bootstrap, bootstrap_otel
from sentinel.data import database
from sentinel.data import db as async_db
from sentinel.interfaces.api.routers.automations.router import router as automations_router
from sentinel.interfaces.api.routers.jobs.router import router as jobs_router
from sentinel.interfaces.api.routers.sre.router import router as sre_router
from sentinel.interfaces.api.routers.support.router import router as support_router
from sentinel.settings import get_settings
from sentinel.utils import logs


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncGenerator[None]:
    bootstrap.initialise()
    bootstrap_otel.init_otel()

    if get_settings().database_url:
        engine = database.get_engine()
        bootstrap_otel.instrument_sqlalchemy(engine=engine.sync_engine)
        await async_db.connect_db()
        logs.log_event("database_engine_initialised")

    yield

    if get_settings().database_url:
        await async_db.disconnect_db()
    await database.close_engine()
    logs.log_event("database_engine_closed")


app = fastapi.FastAPI(
    title="Sentinel",
    description="AI SRE & AI Support Agent",
    version="0.1.0",
    lifespan=lifespan,
)

bootstrap_otel.instrument_fastapi(app=app)

app.mount("/metrics", make_asgi_app())

app.include_router(sre_router, prefix="/api")
app.include_router(support_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(automations_router, prefix="/api")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "sentinel"}


@app.get("/", include_in_schema=False)
async def root() -> fastapi.responses.RedirectResponse:
    return fastapi.responses.RedirectResponse(url="/docs")
