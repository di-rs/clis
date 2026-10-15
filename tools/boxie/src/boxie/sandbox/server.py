"""Private authenticated HTTP/WS adapter for one privileged agent process."""

import asyncio
import hmac
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Query, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.websockets import WebSocketState

from boxie.bootstrap.config import AgentConfig
from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal, TerminationReason
from boxie.persistence.artifacts import ArtifactStore
from boxie.sandbox.docker import DockerPort
from boxie.sandbox.protocol import (
    ConsumerId,
    ControlRequest,
    EnsureRequest,
    ProfileRequest,
    RemoveRequest,
    SnapshotWire,
    SubmitRequest,
    ViewWire,
)
from boxie.sandbox.registry import Agent


def create_agent_app(
    config: AgentConfig,
    docker: DockerPort,
    artifacts: ArtifactStore,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    *,
    credential: bytes,
) -> FastAPI:
    agent = Agent(config, docker, artifacts, clock, monotonic)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await agent.start()
        try:
            yield
        finally:
            await agent.close()

    def authorized(header: str) -> bool:
        return hmac.compare_digest(header.encode(), b"Bearer " + credential)

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.agent = agent

    @app.get("/readyz")
    async def readiness() -> JSONResponse:
        try:
            async with asyncio.timeout(config.docker_timeout):
                ready = await docker.ready()
        except Exception:
            ready = False
        return JSONResponse(
            {
                "identity": {
                    "installation_id": str(config.identity.installation_id),
                    "host_id": config.identity.host_id,
                },
                "dependencies": {"docker": ready},
            },
            status_code=200 if ready else 503,
        )

    @app.exception_handler(BoxieError)
    async def application_error(_request: Request, error: BoxieError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.middleware("http")
    async def request_bounds(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not authorized(request.headers.get("Authorization", "")):
            return JSONResponse({"detail": "invalid agent credential"}, status_code=401)
        # Streaming uploads have their own actual-byte counter. JSON control messages
        # are bounded before FastAPI parses them, including chunked request bodies.
        if request.method == "POST":
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({"detail": "request too large"}, status_code=413)
            request._body = bytes(body)
        return await call_next(request)

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
    async def retire_artifact(
        artifact_id: UUID, consumer_id: Annotated[ConsumerId, Query()]
    ) -> Response:
        await artifacts.retire(Principal(consumer_id), artifact_id)
        return Response(status_code=204)

    @app.post("/v1/profiles/resolve")
    async def profile(body: ProfileRequest) -> SnapshotWire:
        return SnapshotWire.from_domain(
            agent.resolve_profile(body.profile_id, body.command, body.env)
        )

    @app.post("/v1/sandboxes")
    async def submit(body: SubmitRequest) -> ViewWire:
        return ViewWire.from_domain(
            await agent.submit(
                Principal(body.consumer_id), body.idempotency_key, body.spec.domain()
            )
        )

    @app.get("/v1/sandboxes/{sandbox_id}")
    async def get(sandbox_id: UUID, consumer_id: Annotated[ConsumerId, Query()]) -> ViewWire:
        return ViewWire.from_domain(await agent.get(Principal(consumer_id), sandbox_id))

    @app.delete("/v1/sandboxes/{sandbox_id}")
    async def terminate(
        sandbox_id: UUID,
        consumer_id: Annotated[ConsumerId, Query()],
        reason: TerminationReason = TerminationReason.REQUESTED,
    ) -> ViewWire:
        return ViewWire.from_domain(
            await agent.terminate(Principal(consumer_id), sandbox_id, reason)
        )

    @app.post("/v1/sandboxes/{sandbox_id}/ensure")
    async def ensure(sandbox_id: UUID, body: EnsureRequest) -> ViewWire:
        agent.owned(Principal(body.consumer_id), sandbox_id)
        return ViewWire.from_domain(
            await agent.ensure(
                body.identity.domain(),
                sandbox_id,
                body.generation,
                body.snapshot.domain(),
                body.artifact_id,
                body.deadline,
            )
        )

    @app.post("/v1/sandboxes/{sandbox_id}/inspect")
    async def inspect(sandbox_id: UUID, body: ControlRequest) -> ViewWire:
        agent.owned(Principal(body.consumer_id), sandbox_id)
        return ViewWire.from_domain(
            await agent.inspect(body.identity.domain(), sandbox_id, body.generation)
        )

    @app.post("/v1/sandboxes/{sandbox_id}/remove")
    async def remove(sandbox_id: UUID, body: RemoveRequest) -> ViewWire:
        agent.owned(Principal(body.consumer_id), sandbox_id)
        return ViewWire.from_domain(
            await agent.remove(body.identity.domain(), sandbox_id, body.generation, body.reason)
        )

    @app.websocket("/v1/sandboxes/{sandbox_id}/terminal", dependencies=[])
    async def terminal(
        websocket: WebSocket, sandbox_id: UUID, consumer_id: Annotated[ConsumerId, Query()]
    ) -> None:
        if not authorized(websocket.headers.get("Authorization", "")):
            await websocket.send_denial_response(
                JSONResponse({"detail": "invalid agent credential"}, status_code=401)
            )
            return
        try:
            record = agent.owned(Principal(consumer_id), sandbox_id)
            session = agent.terminals.get(record.id)
            if session is None:
                raise BoxieError(409, "terminal is unavailable")
            await session.serve(websocket)
        except BoxieError as error:
            if websocket.application_state == WebSocketState.CONNECTING:
                detail = "resource not found" if error.status == 404 else "terminal unavailable"
                await websocket.send_denial_response(
                    JSONResponse({"detail": detail}, status_code=error.status)
                )
            elif websocket.application_state == WebSocketState.CONNECTED:
                await websocket.close(code=1008)

    return app
