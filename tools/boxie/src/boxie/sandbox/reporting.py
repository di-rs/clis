"""Bounded latest-value reporting. Input is coalesced; lifecycle edges flush promptly."""

import asyncio
import time
from uuid import UUID

import httpx

from boxie.domain.lifecycle import OrchestratorObservation
from boxie.sandbox.protocol import ObservationWire


def edge(item: OrchestratorObservation) -> tuple[object, ...]:
    return (
        item.boot_id,
        item.state,
        item.attachment_epoch,
        item.disconnected_at,
        item.removal_confirmed,
        item.cleanup_pending,
        item.resource_present,
    )


class ObservationReporter:
    def __init__(self, api_url: str, credential: bytes) -> None:
        self.client = httpx.AsyncClient(
            base_url=api_url,
            headers={"Authorization": "Bearer " + credential.decode("ascii")},
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        )
        self.latest: dict[UUID, OrchestratorObservation] = {}
        self.sent: dict[UUID, tuple[tuple[object, ...], float, int]] = {}
        self.changed = asyncio.Event()

    def offer(self, observation: OrchestratorObservation) -> None:
        previous = self.latest.get(observation.sandbox_id)
        if previous is None or observation.event_version > previous.event_version:
            self.latest[observation.sandbox_id] = observation
            self.changed.set()

    def forget(self, identifier: UUID) -> None:
        self.latest.pop(identifier, None)
        self.sent.pop(identifier, None)

    async def flush(self) -> None:
        for identifier, item in list(self.latest.items()):
            previous = self.sent.get(identifier)
            now = time.monotonic()
            if previous and (
                previous[2] >= item.event_version
                or (previous[0] == edge(item) and now - previous[1] < 10)
            ):
                continue
            try:
                async with asyncio.timeout(5):
                    response = await self.client.put(
                        "/internal/observations",
                        json=ObservationWire.from_domain(item).model_dump(mode="json"),
                    )
                if response.status_code in (200, 204) and self.latest.get(identifier) is item:
                    self.sent[identifier] = (edge(item), now, item.event_version)
                    if self.latest.get(identifier) == item and item.removal_confirmed:
                        self.latest.pop(identifier, None)
                        self.sent.pop(identifier, None)
            except httpx.HTTPError, TimeoutError:
                pass  # Retain one latest fact; inspections repair reports lost on restart.

    async def run(self) -> None:
        while True:
            self.changed.clear()
            await self.flush()
            try:
                await asyncio.wait_for(self.changed.wait(), 1)
            except TimeoutError:
                pass

    async def close(self) -> None:
        await self.client.aclose()
