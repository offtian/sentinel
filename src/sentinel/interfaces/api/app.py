from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import fastapi

from sentinel import bootstrap
from sentinel.interfaces.api.routers.sre.router import router as sre_router
from sentinel.interfaces.api.routers.support.router import router as support_router


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncGenerator[None, None]:
    bootstrap.initialise()
    yield


app = fastapi.FastAPI(
    title="Sentinel",
    description="AI SRE & AI Support Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(sre_router, prefix="/api")
app.include_router(support_router, prefix="/api")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "sentinel"}
