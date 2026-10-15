"""Application wiring, static assets, and process lifecycle."""

import asyncio
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from playground.boxie_client import BoxieClient, HttpBoxieClient
from playground.config import Settings
from playground.routes import RequestHostBoundary, create_router
from playground.service import PlaygroundService
from playground.session_store import SessionStore
from playground.storage import EnvironmentStore

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None, *, boxie: BoxieClient | None = None) -> FastAPI:
    settings = settings or Settings()
    if boxie is None:
        secret = settings.boxie_credential_file
        if not stat.S_ISREG(secret.lstat().st_mode) or secret.lstat().st_mode & 0o077:
            raise ValueError("Boxie credential must be a private regular file")
        with os.fdopen(os.open(secret, os.O_RDONLY | os.O_NOFOLLOW), "r") as stream:
            credential = stream.read(4097).strip()
        if not 16 <= len(credential) <= 4096 or any(not 33 <= ord(c) <= 126 for c in credential):
            raise ValueError("invalid Boxie credential file")
        boxie = HttpBoxieClient(settings.boxie_url, credential)
    service = PlaygroundService(
        settings, EnvironmentStore(settings), SessionStore(settings.data_dir), boxie
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        service.store.remove_abandoned_uploads()
        await service.recover()
        cleanup = asyncio.create_task(service.cleanup_periodically())
        try:
            yield
        finally:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
            await service.boxie.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestHostBoundary, settings=service.settings)
    app.state.service = service
    app.state.settings = service.settings
    app.state.store = service.store
    app.state.sessions = service.sessions
    app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    app.include_router(create_router(service, STATIC_DIR))
    return app
