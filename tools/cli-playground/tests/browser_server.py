"""Explicit local Chromium fixture. Never imported by service composition."""

import asyncio
import base64
import tempfile
from pathlib import Path
from typing import Any

from boxie_test_support.orchestrator import FakeTerminal
from fastapi import Request

from tests.boxie_network import boxie_network, product_server, publish


class ObservedTerminal(FakeTerminal):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.writes.append(data)
        await super().write(data)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="boxie-browser-") as directory:
        root = Path(directory)
        async with boxie_network(root / "orchestrator") as network:

            async def terminal(identifier: str) -> ObservedTerminal:
                stream = ObservedTerminal()
                network.daemon.streams[identifier] = stream
                return stream

            network.daemon.attach = terminal
            async with product_server(
                root / "product", str(network.api.base_url), port=8765
            ) as product:
                published = await publish(product, "a" * 40)
                assert published.status_code == 201, published.text
                environment = published.json()["id"]

                @product.app.get("/__test__/state")
                async def state() -> dict[str, Any]:
                    return {
                        "environment": environment,
                        "sandboxes": [
                            {
                                "id": str(record.id),
                                "page_session_ids": [
                                    str(binding.id)
                                    for binding in product.service.sessions.bindings()
                                    if binding.sandbox.id == record.id
                                ],
                                "epoch": record.attachment_epoch,
                                "attached": record.attached,
                                "state": record.state,
                                "last_input_at": str(record.last_input_at),
                                "reason": record.reason,
                                "writes": [
                                    base64.b64encode(data).decode()
                                    for data in network.daemon.streams[record.container_id].writes
                                ]
                                if record.container_id in network.daemon.streams
                                else [],
                                "sizes": network.daemon.streams[record.container_id].sizes
                                if record.container_id in network.daemon.streams
                                else [],
                            }
                            for record in network.orchestrator.records.values()
                        ],
                    }

                @product.app.post("/__test__/output")
                async def output(request: Request) -> dict[str, bool]:
                    value = await request.json()
                    record = next(
                        record
                        for record in network.orchestrator.records.values()
                        if str(record.id) == value["id"]
                    )
                    stream = network.daemon.streams[record.container_id]
                    for encoded in value["chunks"]:
                        stream.queue.put_nowait(base64.b64decode(encoded))
                        await asyncio.sleep(0)
                    return {"ok": True}

                await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
