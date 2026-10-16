"""Orchestrator-only observation ingress; consumer credentials cannot publish execution facts."""

from fastapi import APIRouter

from boxie.application.ports import ObservationSink
from boxie.contracts.execution import ObservationWire


def observation_routes(sink: ObservationSink) -> APIRouter:
    router = APIRouter()

    @router.put("/internal/observations")
    async def report(body: ObservationWire) -> dict[str, bool]:
        return {"accepted": await sink.record_observation(body.domain())}

    return router
