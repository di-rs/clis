"""Application wiring, static assets, and process lifecycle."""

import asyncio
import os
import stat
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from cli_playground.boxie_client import HttpBoxieClient
from cli_playground.config import Settings
from cli_playground.database import adapt_store, check_schema, session_factory
from cli_playground.ports import PersistenceUnavailable
from cli_playground.routes import create_router
from cli_playground.sandbox import BoxieClient
from cli_playground.service import CliPlaygroundService
from cli_playground.session_store import SessionStore
from cli_playground.storage import EnvironmentStore
from cli_playground.transport import RequestHostBoundary

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    settings: Settings | None = None,
    *,
    boxie: BoxieClient | None = None,
    static_dir: Path = STATIC_DIR,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
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
    sessions = session_factory(str(settings.database_url))
    service = CliPlaygroundService(
        settings,
        adapt_store(
            sessions,
            lambda session: EnvironmentStore(
                session, preview_seconds=settings.preview_seconds, clock=clock
            ),
        ),
        adapt_store(sessions, SessionStore),
        boxie,
        clock=clock,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        cleanup: asyncio.Task[None] | None = None
        try:
            await check_schema(sessions)
            service.staging.recover(
                {
                    intent.artifact.artifact_id
                    for intent in await service.sessions(lambda repository: repository.uploads())
                }
            )
            await service.recover()
            cleanup = asyncio.create_task(service.cleanup_periodically())
            yield
        finally:
            if cleanup is not None:
                cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
            await service.boxie.close()
            await sessions.kw["bind"].dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(PersistenceUnavailable)
    async def database_unavailable(
        _request: Request, _error: PersistenceUnavailable
    ) -> JSONResponse:
        return JSONResponse({"detail": "playground database unavailable"}, status_code=503)

    app.add_middleware(RequestHostBoundary, settings=service.settings)
    app.state.service = service
    app.state.settings = service.settings
    app.state.store = service.store
    app.state.sessions = service.sessions
    app.mount("/static", StaticFiles(directory=static_dir, check_dir=False), name="static")
    app.include_router(create_router(service, static_dir))
    return app
