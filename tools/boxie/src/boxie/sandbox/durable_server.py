"""Authenticated SQL-admitted execution transport. No consumer admission routes."""

import asyncio
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import ExpiryRequest, SandboxRef
from boxie.domain.models import Principal, SandboxState
from boxie.domain.policy import validate_policy
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie.sandbox.lifecycle import DurableAgent
from boxie.sandbox.protocol import (
    AdmissionProfileRequest,
    ConsumerId,
    DurableEnsureWire,
    ExecutionControlWire,
    ExecutionExpiryWire,
    ObservationWire,
    SnapshotWire,
)
from boxie.sandbox.reporting import ObservationReporter
from boxie.sandbox.terminal_session import serve_terminal


def create_durable_agent_app(
    agent: DurableAgent,
    artifacts: PostgresArtifactStore,
    *,
    credential: bytes,
    reporter: ObservationReporter,
) -> FastAPI:
    agent.observer = reporter.offer
    agent.forget_observation = reporter.forget

    async def collect() -> None:
        while True:
            try:
                await artifacts.collect()
                await artifacts.collect_orphans()
            except Exception:
                pass  # SQL intents remain pending; no deletion/absence is inferred.
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await agent.start()
        reporting = asyncio.create_task(reporter.run())
        collection = asyncio.create_task(collect())
        try:
            yield
        finally:
            await agent.close()
            reporting.cancel()
            collection.cancel()
            await asyncio.gather(reporting, collection, return_exceptions=True)
            await reporter.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.agent = agent

    def authorized(value: str) -> bool:
        return hmac.compare_digest(value.encode(), b"Bearer " + credential)

    @app.middleware("http")
    async def authenticate(request: Request, next: RequestResponseEndpoint) -> Response:
        if not authorized(request.headers.get("Authorization", "")):
            return JSONResponse({"detail": "invalid agent credential"}, status_code=401)
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
        return {"identity": asdict(agent.config.identity), "boot_id": agent.boot_id}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        try:
            async with asyncio.timeout(5):
                docker_ready = await agent.docker.ready()
                async with artifacts.pool.connection() as conn:
                    await conn.execute("SELECT 1")
                sql_ready = True
        except Exception:
            docker_ready = sql_ready = False
        dependencies = {
            "docker": docker_ready,
            "postgres": sql_ready,
            "execution": agent.execution_ready,
        }
        return JSONResponse(
            {
                "identity": {
                    "installation_id": str(agent.config.identity.installation_id),
                    "host_id": agent.config.identity.host_id,
                },
                "dependencies": dependencies,
            },
            status_code=200 if all(dependencies.values()) else 503,
        )

    @app.post("/v1/profiles/resolve")
    async def profile(body: AdmissionProfileRequest) -> SnapshotWire:
        validate_policy(body.policy.domain(), agent.config.policy_maximum)
        return SnapshotWire.from_domain(
            agent.profiles.resolve_profile(body.profile_id, body.command, body.env)
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

    def boot(expected: UUID) -> None:
        if expected != agent.boot_id:
            raise BoxieError(409, "agent boot changed")

    async def owned(identifier: UUID, body: ExecutionControlWire) -> SandboxRef:
        boot(body.boot_id)
        ref = SandboxRef(identifier, body.generation)
        item = await agent.inspect(ref)
        if item.principal != Principal(body.consumer_id) or item.identity != body.identity.domain():
            raise BoxieError(404, "execution not found")
        return ref

    @app.post("/v1/sandboxes/{identifier}/ensure")
    async def ensure(
        identifier: UUID, body: DurableEnsureWire, x_agent_boot: Annotated[UUID, Header()]
    ) -> ObservationWire:
        boot(x_agent_boot)
        if body.ref.id != identifier:
            raise BoxieError(422, "execution identity differs")
        return ObservationWire.from_domain(await agent.ensure(body.domain()))

    @app.post("/v1/sandboxes/{identifier}/inspect")
    async def inspect(identifier: UUID, body: ExecutionControlWire) -> ObservationWire:
        return ObservationWire.from_domain(await agent.inspect(await owned(identifier, body)))

    @app.post("/v1/sandboxes/{identifier}/renew")
    async def renew(identifier: UUID, body: ExecutionControlWire) -> ObservationWire:
        return ObservationWire.from_domain(
            await agent.renew_control_lease(await owned(identifier, body), body.boot_id)
        )

    @app.post("/v1/sandboxes/{identifier}/remove")
    async def remove(identifier: UUID, body: ExecutionControlWire) -> ObservationWire:
        return ObservationWire.from_domain(
            await agent.remove(await owned(identifier, body), body.reason)
        )

    @app.post("/v1/sandboxes/{identifier}/expire")
    async def expire(identifier: UUID, body: ExecutionExpiryWire) -> ObservationWire:
        decision = await agent.expire_if_current(
            ExpiryRequest(
                await owned(identifier, body),
                body.reason,
                body.observed_event_version,
                body.boot_id,
            )
        )
        return ObservationWire.from_domain(decision.observation)

    @app.websocket("/v1/sandboxes/{identifier}/terminal")
    async def terminal(
        websocket: WebSocket, identifier: UUID, consumer_id: Annotated[ConsumerId, Query()]
    ) -> None:
        if not authorized(websocket.headers.get("Authorization", "")):
            await websocket.send_denial_response(
                JSONResponse({"detail": "invalid agent credential"}, status_code=401)
            )
            return
        record = agent.records.get(identifier)
        if record is None or record.consumer_id != consumer_id:
            await websocket.send_denial_response(
                JSONResponse({"detail": "execution not found"}, status_code=404)
            )
            return
        if record.state != SandboxState.READY or identifier not in agent.terminals:
            await websocket.send_denial_response(
                JSONResponse({"detail": "terminal unavailable"}, status_code=409)
            )
            return
        await serve_terminal(websocket, agent, record.ref)

    return app
