"""Authenticated SQL-admitted execution transport. No consumer admission routes."""

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import Lifespan

from boxie.application.ports import ArtifactAccess
from boxie.contracts.common import ConsumerId
from boxie.contracts.execution import (
    AdmissionProfileRequest,
    SnapshotWire,
)
from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal
from boxie.domain.policy import validate_policy
from boxie.sandbox.execution.orchestrator import Orchestrator
from boxie.sandbox.terminal.session import serve_terminal


def create_orchestrator_app(
    orchestrator: Orchestrator,
    artifacts: ArtifactAccess,
    *,
    credential: bytes,
    readiness: Callable[[], Awaitable[dict[str, bool]]],
    lifespan: Lifespan[FastAPI],
) -> FastAPI:
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.orchestrator = orchestrator

    def authorized(value: str) -> bool:
        return hmac.compare_digest(value.encode(), b"Bearer " + credential)

    @app.middleware("http")
    async def authenticate(request: Request, next: RequestResponseEndpoint) -> Response:
        if not authorized(request.headers.get("Authorization", "")):
            return JSONResponse({"detail": "invalid orchestrator credential"}, status_code=401)
        if request.method == "POST":
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > 65536:
                    return JSONResponse({"detail": "request too large"}, status_code=413)
                data.extend(chunk)
            request._body = bytes(data)
        return await next(request)

    @app.exception_handler(BoxieError)
    async def application_error(request: Request, error: BoxieError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    @app.get("/v1/identity")
    async def identity() -> dict[str, object]:
        return {"identity": asdict(orchestrator.config.identity), "boot_id": orchestrator.boot_id}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        dependencies = await readiness()
        return JSONResponse(
            {
                "identity": {
                    "installation_id": str(orchestrator.config.identity.installation_id),
                    "host_id": orchestrator.config.identity.host_id,
                },
                "dependencies": dependencies,
            },
            status_code=200 if all(dependencies.values()) else 503,
        )

    @app.post("/v1/profiles/resolve")
    async def profile(body: AdmissionProfileRequest) -> SnapshotWire:
        validate_policy(body.policy.domain(), orchestrator.config.policy_maximum)
        return SnapshotWire.from_domain(
            orchestrator.profiles.resolve_profile(body.profile_id, body.command, body.env)
        )

    @app.put("/v1/artifacts/{artifact_id}")
    async def put_artifact(
        artifact_id: UUID,
        request: Request,
        response: Response,
        consumer_id: Annotated[ConsumerId, Query()],
        x_content_sha256: Annotated[str, Header()],
    ) -> dict[str, object]:
        result = await artifacts.put(
            Principal(consumer_id), artifact_id, x_content_sha256, request.stream()
        )
        response.status_code = 201 if result.created else 200
        return asdict(result.artifact)

    @app.delete("/v1/artifacts/{artifact_id}", status_code=204)
    async def retire(artifact_id: UUID, consumer_id: Annotated[ConsumerId, Query()]) -> Response:
        await artifacts.retire(Principal(consumer_id), artifact_id)
        return Response(status_code=204)

    @app.websocket("/v1/sandboxes/{identifier}/terminal")
    async def terminal(
        websocket: WebSocket, identifier: UUID, consumer_id: Annotated[ConsumerId, Query()]
    ) -> None:
        if not authorized(websocket.headers.get("Authorization", "")):
            await websocket.send_denial_response(
                JSONResponse({"detail": "invalid orchestrator credential"}, status_code=401)
            )
            return
        try:
            ref = orchestrator.terminal_ref(Principal(consumer_id), identifier)
        except BoxieError as error:
            await websocket.send_denial_response(
                JSONResponse({"detail": str(error)}, status_code=error.status)
            )
            return
        await serve_terminal(websocket, orchestrator, ref)

    return app
