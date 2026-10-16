"""Bounded reporter behavior independent of external clocks and dependencies."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from boxie.domain.lifecycle import OrchestratorObservation
from boxie.domain.models import OrchestratorIdentity, Principal, SandboxState
from boxie.sandbox.transport.reporting import ObservationReporter


def test_input_is_coalesced_edges_are_immediate_and_outage_keeps_latest() -> None:
    async def exercise() -> None:

        elapsed = 0.0
        delivered: list[int] = []
        unavailable = False

        async def transport(request: httpx.Request) -> httpx.Response:
            import json

            assert request.headers["Authorization"] == "Bearer reporter-orchestrator-secret"
            if unavailable:
                return httpx.Response(503)
            delivered.append(json.loads(request.content)["event_version"])
            return httpx.Response(200, json={"accepted": True})

        reporter = ObservationReporter(
            "http://api.invalid",
            b"reporter-orchestrator-secret",
            clock=lambda: elapsed,
            transport=httpx.MockTransport(transport),
        )
        observation = OrchestratorObservation(
            uuid4(),
            Principal("one"),
            OrchestratorIdentity(uuid4(), "host"),
            1,
            uuid4(),
            1,
            datetime.now(UTC),
            SandboxState.READY,
            True,
        )
        try:
            reporter.offer(observation)
            await reporter.flush()
            assert delivered == [1]
            reporter.offer(replace(observation, event_version=2, last_input_at=datetime.now(UTC)))
            elapsed = 1
            await reporter.flush()
            assert delivered == [1]
            reporter.offer(replace(observation, event_version=3, attachment_epoch=1))
            await reporter.flush()
            assert delivered == [1, 3]
            reporter.offer(
                replace(
                    observation,
                    event_version=4,
                    attachment_epoch=1,
                    last_input_at=datetime.now(UTC),
                )
            )
            elapsed = 11
            await reporter.flush()
            assert delivered == [1, 3, 4]
            unavailable = True
            elapsed = 30
            for version in range(5, 10005):
                reporter.offer(replace(observation, event_version=version, attachment_epoch=2))
            await reporter.flush()
            assert len(reporter.latest) == 1 and delivered == [1, 3, 4]
            unavailable = False
            await reporter.flush()
            assert delivered == [1, 3, 4, 10004]
        finally:
            await reporter.close()

    asyncio.run(exercise())
