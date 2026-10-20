"""Explicit local Chromium fixture. Never imported by service composition."""

import asyncio
import base64
import tempfile
from pathlib import Path
from typing import Any

from boxie_test_support.supervisor import FakeTerminal
from boxie_test_support.terminal_network import EchoDaemon
from fastapi import Request

from tests.boxie_network import boxie_network, product_server, publish


class ObservedTerminal(FakeTerminal):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.writes.append(data)
        await super().write(data)


class ObservedDaemon(EchoDaemon):
    def __init__(self) -> None:
        super().__init__()
        self.observed: dict[str, ObservedTerminal] = {}

    async def attach(self, identifier: str) -> ObservedTerminal:
        stream = ObservedTerminal()
        self.streams[identifier] = stream
        self.observed[identifier] = stream
        return stream


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="boxie-browser-") as directory:
        root = Path(directory)
        daemon = ObservedDaemon()
        async with boxie_network(root / "supervisor", daemon=daemon) as network:
            async with product_server(
                root / "product", str(network.api.base_url), port=8765
            ) as product:
                published = await publish(product, "a" * 40)
                assert published.status_code == 201, published.text
                environment = published.json()["id"]

                @product.app.get("/__test__/state")
                async def state() -> dict[str, Any]:
                    bindings = await product.service.sessions(
                        lambda repository: repository.bindings()
                    )
                    sandboxes = []
                    for record in network.supervisor.records.values():
                        stream = (
                            daemon.observed.get(record.container_id)
                            if record.container_id is not None
                            else None
                        )
                        sandboxes.append(
                            {
                                "id": str(record.id),
                                "page_session_ids": [
                                    str(binding.id)
                                    for binding in bindings
                                    if binding.sandbox.id == record.id
                                ],
                                "epoch": record.attachment_epoch,
                                "attached": record.attached,
                                "state": record.state,
                                "last_input_at": str(record.last_input_at),
                                "reason": record.reason,
                                "writes": [
                                    base64.b64encode(data).decode() for data in stream.writes
                                ]
                                if stream is not None
                                else [],
                                "sizes": stream.sizes if stream is not None else [],
                            }
                        )
                    return {"environment": environment, "sandboxes": sandboxes}

                @product.app.post("/__test__/output")
                async def output(request: Request) -> dict[str, bool]:
                    value = await request.json()
                    record = next(
                        record
                        for record in network.supervisor.records.values()
                        if str(record.id) == value["id"]
                    )
                    assert record.container_id is not None
                    stream = daemon.observed[record.container_id]
                    for encoded in value["chunks"]:
                        stream.queue.put_nowait(base64.b64decode(encoded))
                        await asyncio.sleep(0)
                    return {"ok": True}

                await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
