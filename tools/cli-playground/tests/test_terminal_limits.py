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
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from .boxie_network import admit, boxie_network, product_server, publish, socket_url
from .helpers import upload


@pytest.mark.parametrize("limit", [16777216, 10], ids=["default-cap", "lower-orchestrator-cap"])
def test_orchestrator_output_limit_reaches_browser_at_exact_cap(
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
        async with boxie_network(tmp_path / "orchestrator") as network:
            profiles = json.loads(network.config.profiles_file.read_text())
            if limit != 16777216:
                profiles["shell"]["limits"] = {"output_bytes": limit}
                network.config.profiles_file.write_text(json.dumps(profiles))
                network.orchestrator.profiles.reload()
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

        async def receive(self) -> bytes | None:
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


@pytest.mark.parametrize(
    "phase,kind,close_code",
    [("before", "ack", 1000), ("after", "ack", 1011)]
    + [
        (phase, kind, 1000)
        for phase in ("during", "after")
        for kind in ("ack", "heartbeat", "input", "resize")
    ],
)
def test_final_control_preserves_buffered_product_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, kind: str, close_code: int
) -> None:
    from urllib.parse import urlsplit

    from boxie_test_support.terminal_completion import CompletionBarrier
    from playground.boxie_client import HttpTerminal

    async def exercise() -> None:
        barrier = CompletionBarrier(monkeypatch, HttpTerminal, phase, kind, close_code)
        async with boxie_network(tmp_path / "orchestrator") as network:
            profiles = json.loads(network.config.profiles_file.read_text())
            profiles["shell"]["limits"] = {"output_bytes": 10}
            network.config.profiles_file.write_text(json.dumps(profiles))
            network.orchestrator.profiles.reload()
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                barrier.port = urlsplit(str(product.http.base_url)).port
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
                    next(iter(network.daemon.terminals.values())).queue.put_nowait(b"last")
                    assert await asyncio.wait_for(ws.recv(), 1) == (2).to_bytes(8, "big") + b"last"
                    if close_code != 1000:
                        with pytest.raises(ConnectionClosed):
                            await barrier.finish(ws, epoch)
                        assert ws.close_code == 1011
                        return
                    assert json.loads(await barrier.finish(ws, epoch)) == {
                        "type": "status",
                        "reason": "output_limit",
                        "message": "Session ended: output_limit.",
                    }
                    await asyncio.wait_for(ws.wait_closed(), 1)
                    assert ws.close_code == 1000

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "scenario",
    [
        "stale-epoch",
        "future-ack",
        "backward-ack",
        "stale-heartbeat",
        "stale-input",
        "stale-resize",
        "flood-stalled",
        "detach",
        "malformed",
        "disconnect",
        "transport-error",
        "send-timeout",
        "missing-status",
        "stalled-status",
    ],
)
def test_closed_product_control_drain_keeps_abort_and_error_boundaries(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    from boxie_test_support.terminal_completion import failed_drain
    from playground import transport
    from playground.boxie_client import BoxieUnavailable, TerminalWriteClosed
    from playground.terminal_protocol import response

    async def exercise() -> None:
        monkeypatch.setattr(transport, "TERMINAL_DRAIN_TIMEOUT", 0.02)
        result = await failed_drain(
            transport.bridge_terminal,
            TerminalWriteClosed("closed"),
            BoxieUnavailable("transport failed"),
            scenario,
            None,
            decode_event=response.validate_json,
        )
        if scenario == "malformed":
            assert isinstance(result, ValueError)
        elif scenario == "disconnect":
            assert isinstance(result, WebSocketDisconnect)
        elif scenario in {"send-timeout", "stalled-status", "flood-stalled"}:
            assert isinstance(result, TimeoutError)
        else:
            assert isinstance(result, BoxieUnavailable)

    asyncio.run(exercise())
