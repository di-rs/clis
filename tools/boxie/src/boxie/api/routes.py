"""Consumer schemas exclude all orchestrator ownership and privileged configuration."""

from dataclasses import asdict
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from boxie.application.service import Service
from boxie.domain.models import Principal, TerminationReason
from boxie.sandbox.protocol import SpecWire, ViewWire


def principal(request: Request) -> Principal:
    return cast(Principal, request.state.principal)


def routes(service: Service) -> APIRouter:
    router = APIRouter()

    @router.put("/v1/artifacts/{artifact_id}")
    async def put(
        artifact_id: UUID,
        request: Request,
        x_content_sha256: Annotated[str, Header(pattern=r"^[a-f0-9]{64}$")],
    ) -> JSONResponse:
        result = await service.put(
            principal(request), artifact_id, x_content_sha256, request.stream()
        )
        return JSONResponse(
            jsonable_encoder(asdict(result.artifact)), status_code=201 if result.created else 200
        )

    @router.delete("/v1/artifacts/{artifact_id}", status_code=204)
    async def retire(artifact_id: UUID, request: Request) -> Response:
        await service.artifacts.retire(principal(request), artifact_id)
        return Response(status_code=204)

    @router.post("/v1/sandboxes", status_code=202)
    async def submit(
        body: SpecWire,
        request: Request,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    ) -> ViewWire:
        return ViewWire.from_domain(
            await service.lifecycle.submit(principal(request), idempotency_key, body.domain())
        )

    @router.get("/v1/sandboxes/{sandbox_id}")
    async def get(sandbox_id: UUID, request: Request) -> ViewWire:
        return ViewWire.from_domain(await service.lifecycle.get(principal(request), sandbox_id))

    @router.delete("/v1/sandboxes/{sandbox_id}")
    async def terminate(sandbox_id: UUID, request: Request) -> ViewWire:
        return ViewWire.from_domain(
            await service.lifecycle.terminate(
                principal(request), sandbox_id, TerminationReason.REQUESTED
            )
        )

    return router
