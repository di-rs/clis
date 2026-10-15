"""Handshake transport failures must not erase the original READY grace."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from boxie.domain.errors import BoxieError
from boxie_test_support.agent import (
    Clock,
    DockerDaemon,
    configuration,
    live,
    request,
    settled,
    upload,
)
from starlette.types import Message
from starlette.websockets import WebSocket


@pytest.mark.parametrize(
    ("failure", "elapsed"), [("error", 0), ("error", 119), ("cancel", 119), ("pending", 119)]
)
def test_unsuccessful_first_accept_preserves_original_grace(
    tmp_path: Path, failure: str, elapsed: int
) -> None:
    async def exercise() -> None:
        clock, daemon = Clock(), DockerDaemon()
        async with live(configuration(tmp_path), daemon, clock) as (client, agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            await client.delete(f"/v1/artifacts/{artifact}", params={"consumer_id": "one"})
            terminal = agent.terminals[UUID(identifier)]
            accepting = asyncio.Event()

            async def receive() -> Message:
                return {"type": "websocket.connect"}

            async def send(message: Message) -> None:
                assert message["type"] == "websocket.accept"
                accepting.set()
                if failure == "error":
                    raise OSError("peer disconnected during acceptance")
                await asyncio.Event().wait()

            clock.advance(elapsed)
            websocket = WebSocket({"type": "websocket"}, receive, send)
            serving = asyncio.create_task(terminal.serve(websocket))
            try:
                await asyncio.wait_for(accepting.wait(), 1)
                if failure == "error":
                    with pytest.raises(OSError, match="peer disconnected"):
                        await serving
                elif failure == "cancel":
                    serving.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await serving
                # Even a failed or pending first attempt keeps PR1's one-attempt fence.
                with pytest.raises(BoxieError) as denial:
                    await terminal.serve(WebSocket({"type": "websocket"}, receive, send))
                assert denial.value.status == 409
                clock.advance(119 - elapsed)
                await asyncio.sleep(0.03)
                assert agent.reserved_count == 1 and agent.artifact_exists(artifact)
                assert not daemon.removed
                clock.advance(1)
                result = await settled(client, identifier, "terminated")
                assert result["reason"] == "disconnected"
                assert agent.reserved_count == 0 and not daemon.configs
                assert not agent.artifact_exists(artifact)
                assert terminal.closed
            finally:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_exit_after_successful_accept_starts_normal_disconnect_grace(
    tmp_path: Path, failure: str
) -> None:
    async def exercise() -> None:
        clock, daemon = Clock(), DockerDaemon()
        async with live(configuration(tmp_path), daemon, clock) as (client, agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            terminal = agent.terminals[UUID(identifier)]
            output_started = asyncio.Event()
            messages: asyncio.Queue[Message] = asyncio.Queue()
            messages.put_nowait({"type": "websocket.connect"})

            async def receive() -> Message:
                return await messages.get()

            async def send(message: Message) -> None:
                if message["type"] == "websocket.accept":
                    return
                assert message["type"] == "websocket.send"
                output_started.set()
                if failure == "error":
                    raise OSError("peer disconnected after acceptance")
                await asyncio.Event().wait()

            clock.advance(119)
            serving = asyncio.create_task(
                terminal.serve(WebSocket({"type": "websocket"}, receive, send))
            )
            try:
                await asyncio.wait_for(output_started.wait(), 1)
                if failure == "cancel":
                    serving.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await serving
                else:
                    await serving
                clock.advance(119)
                await asyncio.sleep(0.03)
                assert agent.reserved_count == 1 and not daemon.removed
                clock.advance(1)
                assert (await settled(client, identifier, "terminated"))["reason"] == "disconnected"
                assert agent.reserved_count == 0 and not daemon.configs
            finally:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)

    asyncio.run(exercise())
