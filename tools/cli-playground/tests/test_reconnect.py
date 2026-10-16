"""Actual product-hop reconnect, browser-path heartbeat and bounded ASGI delivery."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest
from boxie_test_support.terminal_network import attach
from fastapi import WebSocket
from playground import routes
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from .boxie_network import admit, boxie_network, product_server, publish, socket_url


def test_failed_resume_cannot_stop_healthy_successor_and_browser_loss_crosses_both_relays(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with boxie_network(tmp_path / "agent") as network:
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                identifier = (await publish(product, "a" * 40)).json()["id"]
                binding = await admit(product, identifier, "a" * 40)
                url = socket_url(product, identifier, binding)
                origin = Origin(str(product.http.base_url).rstrip("/"))
                async with connect(url, origin=origin) as old:
                    await old.send(
                        json.dumps({"type": "auth", "credential": binding["credential"]})
                    )
                    assert json.loads(await old.recv())["type"] == "authenticated"
                    epoch, _ = await attach(old)
                    async with connect(url, origin=origin) as failed:
                        await failed.send(
                            json.dumps({"type": "auth", "credential": binding["credential"]})
                        )
                        await failed.recv()
                        await failed.send(
                            json.dumps(
                                {"type": "attach", "resume_after": 999, "cols": 80, "rows": 24}
                            )
                        )
                        with pytest.raises(ConnectionClosed):
                            await asyncio.wait_for(failed.recv(), 2)
                    saved = product.service.sessions.get(UUID(binding["id"]))
                    assert saved is not None and not saved.pending_stop and saved.attached
                    await old.send(
                        json.dumps(
                            {"type": "input", "epoch": epoch, "source": "user", "data": "healthy"}
                        )
                    )
                    assert await old.recv() == (2).to_bytes(8, "big") + b"healthy"
                    async with connect(url, origin=origin) as new:
                        await new.send(
                            json.dumps({"type": "auth", "credential": binding["credential"]})
                        )
                        await new.recv()
                        successor, replay = await attach(new, cursor=2)
                        assert successor > epoch and replay == b""
                        await old.close()
                        await new.send(
                            json.dumps({"type": "heartbeat", "epoch": successor, "nonce": 1})
                        )
                        assert json.loads(await new.recv()) == {
                            "type": "heartbeat",
                            "epoch": successor,
                            "nonce": 1,
                        }
                        await (await new.ping())
                        network.clock.advance(30)
                        await asyncio.wait_for(new.wait_closed(), 2)
                        record = network.agent.records[saved.sandbox.id]
                        assert not record.attached
                        assert (
                            network.agent.deadlines[record.id].disconnect
                            == network.clock.monotonic() + 120
                        )

    asyncio.run(exercise())


def test_stalled_browser_send_close_and_relay_join_are_finite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(routes, "TERMINAL_SEND_TIMEOUT", 0.02)
        monkeypatch.setattr(routes, "TERMINAL_CLOSE_TIMEOUT", 0.02)

        async def blocked_send(message: object) -> None:
            await asyncio.Event().wait()

        receiving = False

        async def blocked_receive() -> dict[str, object]:
            nonlocal receiving
            receiving = True
            try:
                await asyncio.Event().wait()
            finally:
                receiving = False
            return {}

        class Peer:
            async def receive(self) -> bytes:
                return (1).to_bytes(8, "big") + b"output"

            async def send(self, message: str) -> None:
                pass

        websocket = WebSocket({"type": "websocket"}, blocked_receive, blocked_send)
        from starlette.websockets import WebSocketState

        websocket.application_state = websocket.client_state = WebSocketState.CONNECTED
        async with asyncio.timeout(0.2):
            with pytest.raises(TimeoutError):
                await routes.bridge_terminal(websocket, Peer())
            await routes.close_terminal(websocket)
        assert not receiving

    asyncio.run(exercise())


@pytest.mark.parametrize("in_flight", [False, True])
def test_delayed_old_attach_cannot_replace_a_healthy_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, in_flight: bool
) -> None:
    from boxie.sandbox.client import AgentTerminal

    async def exercise() -> None:
        forwarded, release = asyncio.Event(), asyncio.Event()
        first = True
        original = AgentTerminal.send

        async def delayed(self: AgentTerminal, data: bytes | str) -> None:
            nonlocal first
            if (
                in_flight
                and first
                and isinstance(data, str)
                and json.loads(data)["type"] == "attach"
            ):
                first = False
                # The product already forwarded this request. Delay the independent
                # API hop, beyond any product-local pre-send claim check.
                forwarded.set()
                await release.wait()
            await original(self, data)

        monkeypatch.setattr(AgentTerminal, "send", delayed)
        async with boxie_network(tmp_path / "agent") as network:
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                identifier = (await publish(product, "a" * 40)).json()["id"]
                binding = await admit(product, identifier, "a" * 40)
                url = socket_url(product, identifier, binding)
                origin = Origin(str(product.http.base_url).rstrip("/"))
                async with connect(url, origin=origin) as old:
                    await old.send(
                        json.dumps({"type": "auth", "credential": binding["credential"]})
                    )
                    assert json.loads(await old.recv())["type"] == "authenticated"
                    request = json.dumps(
                        {"type": "attach", "resume_after": 0, "cols": 80, "rows": 24}
                    )
                    if in_flight:
                        await old.send(request)
                        await asyncio.wait_for(forwarded.wait(), 1)
                    try:
                        async with connect(url, origin=origin) as new:
                            await new.send(
                                json.dumps({"type": "auth", "credential": binding["credential"]})
                            )
                            assert json.loads(await new.recv())["type"] == "authenticated"
                            epoch, _ = await attach(new)
                            saved = product.service.sessions.get(UUID(binding["id"]))
                            assert saved is not None
                            record = network.agent.records[saved.sandbox.id]
                            assert record.attachment_epoch == epoch and record.attached
                            grace = vars(network.agent.deadlines[record.id]).copy()
                            if in_flight:
                                release.set()
                            else:
                                await old.send(request)
                            await asyncio.wait_for(old.wait_closed(), 2)
                            await asyncio.sleep(0.3)
                            assert record.attachment_epoch == epoch and record.attached
                            assert new.close_code is None
                            assert vars(network.agent.deadlines[record.id]) == grace
                            assert record.attachment_order == 2
                            async with connect(url, origin=origin) as forged:
                                await forged.send(
                                    json.dumps(
                                        {"type": "auth", "credential": binding["credential"]}
                                    )
                                )
                                await forged.recv()
                                await forged.send(
                                    json.dumps(
                                        {
                                            "type": "attach",
                                            "attachment_order": 999,
                                            "resume_after": 0,
                                            "cols": 80,
                                            "rows": 24,
                                        }
                                    )
                                )
                                await asyncio.wait_for(forged.wait_closed(), 2)
                            assert record.attachment_order == 2 and record.attachment_epoch == epoch
                            assert vars(network.agent.deadlines[record.id]) == grace
                            await new.send(
                                json.dumps(
                                    {
                                        "type": "input",
                                        "epoch": epoch,
                                        "source": "user",
                                        "data": "still healthy",
                                    }
                                )
                            )
                            assert (
                                await asyncio.wait_for(new.recv(), 1)
                                == (2).to_bytes(8, "big") + b"still healthy"
                            )
                            await old.close()
                            saved = product.service.sessions.get(UUID(binding["id"]))
                            assert saved is not None and not saved.pending_stop and saved.attached
                    finally:
                        release.set()

    asyncio.run(exercise())
