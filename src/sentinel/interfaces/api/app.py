from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import fastapi
from prometheus_client import make_asgi_app

from sentinel import bootstrap, bootstrap_otel
from sentinel import config as config_mod
from sentinel.data import database
from sentinel.data import db as async_db
from sentinel.interfaces.api import middleware as api_middleware
from sentinel.interfaces.api.routers.automations.router import router as automations_router
from sentinel.interfaces.api.routers.jobs.router import router as jobs_router
from sentinel.interfaces.api.routers.sre.router import router as sre_router
from sentinel.interfaces.api.routers.support.router import router as support_router
from sentinel.interfaces.graphs import agents as agent_module
from sentinel.interfaces.workflows import _checkpointer as workflows_checkpointer
from sentinel.interfaces.workflows import support_review as workflows_support_review
from sentinel.settings import get_settings
from sentinel.utils import logs


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncGenerator[None]:
    bootstrap.initialise()
    cfg = config_mod.get_config()
    cfg.load_agents(agent_module=agent_module)
    bootstrap_otel.init_otel()

    settings = get_settings()
    saver_close: Callable[[], Awaitable[None]] | None = None
    app.state.support_review_graph = None
    app.state.support_review_checkpointer_close = None

    if settings.database_url:
        engine = database.get_engine()
        bootstrap_otel.instrument_sqlalchemy(engine=engine.sync_engine)
        await async_db.connect_db()
        logs.log_event("database_engine_initialised")

        saver, saver_close = await workflows_checkpointer.build_checkpointer(settings)
        app.state.support_review_graph = workflows_support_review.build_support_review_graph(
            checkpointer=saver
        )
        app.state.support_review_checkpointer_close = saver_close
        logs.log_event("support_review_graph_initialised")

    yield

    if saver_close is not None:
        await saver_close()
        logs.log_event("support_review_checkpointer_closed")
    if settings.database_url:
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

app.add_middleware(api_middleware.RequestIdMiddleware)

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
