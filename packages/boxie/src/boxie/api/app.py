"""Construct an unprivileged private API from injected application ports."""

from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.datastructures import Headers
from starlette.middleware.base import RequestResponseEndpoint

from boxie.api.auth import ConsumerAuth
from boxie.api.routes import routes
from boxie.api.terminal import terminal_routes
from boxie.application.ports import ArtifactAccess, ReadyLifecycle, TerminalAccess
from boxie.domain.errors import BoxieError
from boxie.observability.logging import RequestLogging


class ApiConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    release: str = Field(min_length=1)
    consumers: Mapping[str, bytes] = Field(min_length=1, repr=False)

    @field_validator("consumers")
    @classmethod
    def copy_credentials(cls, value: Mapping[str, bytes]) -> Mapping[str, bytes]:
        return MappingProxyType(dict(value))


def create_app(
    config: ApiConfig,
    lifecycle: ReadyLifecycle,
    artifacts: ArtifactAccess,
    terminal: TerminalAccess,
    *,
    deliver: Callable[[UUID], Awaitable[None]] | None = None,
    request_completed: Callable[[str, str, str, float], None] | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    auth = ConsumerAuth(config.consumers)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, _error: Exception) -> PlainTextResponse:
        return PlainTextResponse(
            "Internal Server Error",
            status_code=500,
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.exception_handler(BoxieError)
    async def application_error(_request: Request, error: BoxieError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    @app.middleware("http")
    async def authenticate_and_bound(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            if request.url.path != "/healthz":
                request.state.principal = auth.resolve(request.headers.get("Authorization", ""))
            if request.method == "POST":
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 65536:
                        return JSONResponse({"detail": "request too large"}, status_code=413)
                    body.extend(chunk)
                request._body = bytes(body)
        except BoxieError as error:
            return JSONResponse({"detail": str(error)}, status_code=error.status)
        return await call_next(request)

    @app.get("/healthz")
    async def health() -> dict[str, bool]:
        return {"alive": True}

    @app.get("/readyz")
    async def readiness() -> JSONResponse:
        ready = await lifecycle.ready()
        return JSONResponse(
            {
                "release": config.release,
                "dependencies": {"orchestration": ready},
            },
            status_code=200 if ready else 503,
        )

    app.include_router(routes(lifecycle, artifacts, deliver))
    app.include_router(terminal_routes(lifecycle, terminal, auth))
    app.add_middleware(
        RequestLogging,
        completed=request_completed,
        accept_incoming=lambda scope: (
            scope.get("path") != "/healthz"
            and auth.accepts(Headers(scope=scope).get("authorization", ""))
        ),
    )
    return app
