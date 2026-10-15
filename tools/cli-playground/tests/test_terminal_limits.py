"""Output caps retain an explicit status at the browser-facing product boundary."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from boxie_test_support.terminal_network import attach
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playground.boxie_client import TerminalPort
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.typing import Origin

from .boxie_network import admit, boxie_network, product_server, publish, socket_url
from .helpers import upload


@pytest.mark.parametrize("limit", [16777216, 10], ids=["default-cap", "lower-agent-cap"])
def test_agent_output_limit_reaches_browser_at_exact_cap(
    tmp_path: Path, limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        if limit == 10:
            from fastapi import WebSocket

            original_send = WebSocket.send_bytes

            async def delayed_send(self: WebSocket, data: bytes) -> None:
                await original_send(self, data)
                # Let the browser ACK the final payload before the sender queues status.
                await asyncio.sleep(0.03)

            monkeypatch.setattr(WebSocket, "send_bytes", delayed_send)
        async with boxie_network(tmp_path / "agent") as network:
            profiles = json.loads(network.config.profiles_file.read_text())
            if limit != 16777216:
                profiles["shell"]["limits"] = {"output_bytes": limit}
                network.config.profiles_file.write_text(json.dumps(profiles))
                network.agent.profiles.reload()
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                identifier = (await publish(product, "a" * 40)).json()["id"]
                binding = await admit(product, identifier, "a" * 40)
                async with connect(
                    socket_url(product, identifier, binding),
                    origin=Origin(str(product.http.base_url).rstrip("/")),
                ) as ws:
                    await ws.send(json.dumps({"type": "auth", "credential": binding["credential"]}))
                    assert json.loads(await ws.recv())["type"] == "authenticated"
                    epoch, initial = await attach(ws)
                    assert initial == b"ready\n"
                    forwarded = len(initial)
                    sequence = 1
                    while forwarded < limit:
                        chunk = b"x" * min(16384, limit - forwarded)
                        next(iter(network.daemon.terminals.values())).queue.put_nowait(chunk)
                        frame = await asyncio.wait_for(ws.recv(), 2)
                        assert isinstance(frame, bytes)
                        sequence += 1
                        assert int.from_bytes(frame[:8], "big") == sequence
                        forwarded += len(frame) - 8
                        await ws.send(
                            json.dumps({"type": "ack", "epoch": epoch, "sequence": sequence})
                        )
                    frame = await ws.recv()
                    assert forwarded == limit
                    assert json.loads(frame) == {
                        "type": "status",
                        "reason": "output_limit",
                        "message": "Session ended: output_limit.",
                    }

    asyncio.run(exercise())


@pytest.mark.parametrize("split", [False, True], ids=["oversized-frame", "extra-frame"])
def test_product_defensive_cap_reports_limit_for_misbehaving_peer(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, split: bool
) -> None:
    class Peer:
        def __init__(self) -> None:
            self.frames = iter(
                [(1).to_bytes(8, "big") + b"x" * 16777216, (2).to_bytes(8, "big") + b"!"]
                if split
                else [(1).to_bytes(8, "big") + b"x" * 16777217]
            )

        async def receive(self) -> bytes | str | None:
            return next(self.frames, None)

        async def send(self, message: str) -> None:
            pass

    @asynccontextmanager
    async def terminal(identifier: UUID) -> AsyncIterator[TerminalPort]:
        yield Peer()

    monkeypatch.setattr(cast(FastAPI, api_client.app).state.service.boxie, "terminal", terminal)
    deployed = upload(api_client)
    binding = api_client.post(
        f"/api/environments/{deployed['id']}/sessions",
        headers={"Origin": "https://play.example"},
        json={"revision": "a" * 40},
    ).json()
    with api_client.websocket_connect(
        f"wss://play.example/ws/{deployed['id']}?session_id={binding['id']}&revision={binding['revision']}",
        headers={"Origin": "https://play.example"},
    ) as ws:
        ws.send_json({"type": "auth", "credential": binding["credential"]})
        assert ws.receive_json()["type"] == "authenticated"
        assert ws.receive_bytes() == (1).to_bytes(8, "big") + b"x" * 16777216
        assert ws.receive_json() == {
            "type": "status",
            "reason": "output_limit",
            "message": "Session ended: output_limit.",
        }
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
