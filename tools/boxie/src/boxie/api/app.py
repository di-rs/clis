"""Construct an unprivileged private API from injected application ports."""

import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from boxie.api.auth import ConsumerAuth
from boxie.api.observations import ObservationSink, observation_routes
from boxie.api.routes import routes
from boxie.api.terminal import terminal_routes
from boxie.application.ports import ArtifactAccess
from boxie.application.service import ReadyLifecycle, Service, TerminalAccess
from boxie.domain.errors import BoxieError
from boxie.observability.logging import RequestLogging


@dataclass(frozen=True)
class ApiConfig:
    release: str
    consumers: Mapping[str, bytes] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.release or not self.consumers:
            raise ValueError("release and consumer credentials required")
        object.__setattr__(self, "consumers", MappingProxyType(dict(self.consumers)))


def create_app(
    config: ApiConfig,
    lifecycle: ReadyLifecycle,
    artifacts: ArtifactAccess,
    terminal: TerminalAccess,
    *,
    observations: ObservationSink | None = None,
    agent_credential: bytes | None = None,
    request_completed: Callable[[str, str, str, float], None] | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    auth = ConsumerAuth(config.consumers)
    service = Service(lifecycle, artifacts, terminal)

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
            if request.url.path == "/internal/observations":
                if (
                    observations is None
                    or agent_credential is None
                    or not hmac.compare_digest(
                        request.headers.get("Authorization", "").encode(),
                        b"Bearer " + agent_credential,
                    )
                ):
                    raise BoxieError(401, "invalid agent credential")
            elif request.url.path != "/healthz":
                request.state.principal = auth.resolve(request.headers.get("Authorization", ""))
            if request.method == "POST" or request.url.path == "/internal/observations":
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
                "dependencies": {"orchestration" if observations else "agent": ready},
            },
            status_code=200 if ready else 503,
        )

    if observations is not None:
        app.include_router(observation_routes(observations))
    app.include_router(routes(service))
    app.include_router(terminal_routes(service, auth))
    app.add_middleware(RequestLogging, completed=request_completed)
    return app
