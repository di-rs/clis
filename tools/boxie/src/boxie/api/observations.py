"""Orchestrator-only observation ingress; consumer credentials cannot publish execution facts."""

from typing import Protocol

from fastapi import APIRouter

from boxie.domain.lifecycle import OrchestratorObservation
from boxie.sandbox.protocol import ObservationWire


class ObservationSink(Protocol):
    async def record_observation(self, observation: OrchestratorObservation) -> bool: ...


def observation_routes(sink: ObservationSink) -> APIRouter:
    router = APIRouter()

    @router.put("/internal/observations")
    async def report(body: ObservationWire) -> dict[str, bool]:
        return {"accepted": await sink.record_observation(body.domain())}

    return router
