"""Application wiring, static assets, and process lifecycle."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from playground.config import Settings
from playground.routes import RequestHostBoundary, create_router
from playground.service import PlaygroundService

STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    service = PlaygroundService(settings or Settings())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if service.settings.reconcile_sessions_on_start:
            await service.sessions.cleanup_orphans()
        service.store.remove_abandoned_uploads()
        await service.cleanup_inactive()
        cleanup = asyncio.create_task(service.cleanup_periodically())
        try:
            yield
        finally:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
            await service.sessions.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestHostBoundary, settings=service.settings)
    app.state.settings = service.settings
    app.state.store = service.store
    app.state.sessions = service.sessions
    app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    app.include_router(create_router(service, STATIC_DIR))
    return app
