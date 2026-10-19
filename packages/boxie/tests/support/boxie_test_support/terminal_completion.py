"""Scheduling barriers for late controls over the real terminal relay sockets."""

import asyncio
import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import WebSocket
from starlette.types import Message


class CompletionBarrier:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        peer_type: Any,
        phase: str,
        kind: str = "ack",
        close_code: int = 1000,
    ) -> None:
        self.port: int | None = None
        self.phase = phase
        self.kind = kind
        self.release = asyncio.Event()
        self.control_sent = asyncio.Event()
        control_started = asyncio.Event()
        self.peer: Any = None
        send_bytes, send_control, close_socket = (
            WebSocket.send_bytes,
            peer_type.send,
            WebSocket.close,
        )

        async def output(socket: WebSocket, data: bytes) -> None:
            await send_bytes(socket, data)
            if int.from_bytes(data[:8], "big") != 2:
                return
            if socket.scope["server"][1] == self.port:
                async with asyncio.timeout(1):
                    await self.release.wait()
            elif phase == "before" and "consumer_id" in socket.query_params:
                async with asyncio.timeout(1):
                    await self.control_sent.wait()

        async def control(peer: Any, data: bytes | str) -> None:
            self.peer = peer
            message = json.loads(data)
            is_final = (
                message["type"] == "ack"
                and message["sequence"] == 2
                or message["type"] == "heartbeat"
                and message["nonce"] == 42
                or message["type"] == "input"
                and message["data"] == "late"
                or message["type"] == "resize"
                and message["cols"] == 101
            )
            if not is_final:
                await send_control(peer, data)
                return
            connection = getattr(peer, "socket", None) or peer._connection
            control_started.set()
            if phase != "before":
                await connection.wait_closed()
            try:
                await send_control(peer, data)
            finally:
                self.control_sent.set()

        async def close(socket: WebSocket, code: int = 1000, reason: str | None = None) -> None:
            if socket.scope["server"][1] != self.port and socket.url.path.endswith("/terminal"):
                if phase == "during":
                    async with asyncio.timeout(1):
                        await control_started.wait()
                code = close_code
            await close_socket(socket, code, reason)

        monkeypatch.setattr(WebSocket, "close", close)
        monkeypatch.setattr(WebSocket, "send_bytes", output)
        monkeypatch.setattr(peer_type, "send", control)

    async def finish(self, socket: Any, epoch: int) -> str | bytes:
        if self.phase == "after":
            connection = getattr(self.peer, "socket", None) or self.peer._connection
            await asyncio.wait_for(connection.wait_closed(), 1)
        frame = {
            "ack": {"type": "ack", "epoch": epoch, "sequence": 2},
            "heartbeat": {"type": "heartbeat", "epoch": epoch, "nonce": 42},
            "input": {"type": "input", "epoch": epoch, "source": "user", "data": "late"},
            "resize": {"type": "resize", "epoch": epoch, "cols": 101, "rows": 30},
        }[self.kind]
        await socket.send(json.dumps(frame))
        await asyncio.wait_for(self.control_sent.wait(), 1)
        receiving = asyncio.create_task(socket.recv())
        try:
            # Output is deliberately held: a close here means the input task
            # cancelled delivery, not that a status raced ahead of the assertion.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(receiving), 0.05)
        finally:
            self.release.set()
        return await asyncio.wait_for(receiving, 1)


async def failed_drain(
    relay: Any,
    clean_close: Exception,
    transport_error: Exception,
    scenario: str,
    eof_error: Exception | None,
    *,
    decode_event: Callable[[str], object] | None = None,
) -> BaseException | None:
    """Exercise aborts while a real ASGI socket has final output in flight."""
    from starlette.websockets import WebSocketState

    sent = asyncio.Event()
    release = asyncio.Event()
    attempted = asyncio.Event()
    primed = asyncio.Event()
    cancelled = asyncio.Event()
    controls: asyncio.Queue[Message] = asyncio.Queue()
    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)
        if message.get("bytes") is not None:
            sent.set()
            try:
                await release.wait()
            finally:
                cancelled.set()

    class Peer:
        def __init__(self) -> None:
            self.frames: Iterator[str | bytes] = iter(
                ['{"type":"attached","epoch":7}', (2).to_bytes(8, "big") + b"last"]
            )

        async def receive(self) -> object:
            frame = next(self.frames, None)
            if frame is not None:
                return (
                    decode_event(frame)
                    if decode_event is not None and isinstance(frame, str)
                    else frame
                )
            if eof_error is not None:
                raise eof_error
            return None

        async def send(self, message: str) -> None:
            if scenario in {"backward-ack", "stale-heartbeat"} and not primed.is_set():
                primed.set()
                return
            attempted.set()
            if scenario == "transport-error":
                raise transport_error
            if scenario == "send-timeout":
                raise TimeoutError
            raise clean_close

    socket = WebSocket({"type": "websocket"}, controls.get, send)
    socket.application_state = socket.client_state = WebSocketState.CONNECTED
    task = asyncio.create_task(relay(socket, Peer()))
    await asyncio.wait_for(sent.wait(), 1)
    frame: dict[str, Any] = {"type": "ack", "epoch": 7, "sequence": 2}
    if scenario in {"backward-ack", "stale-heartbeat"}:
        first = (
            frame if scenario == "backward-ack" else {"type": "heartbeat", "epoch": 7, "nonce": 42}
        )
        controls.put_nowait({"type": "websocket.receive", "text": json.dumps(first)})
        await asyncio.wait_for(primed.wait(), 1)
        frame = {"type": "ack", "epoch": 7, "sequence": 1} if scenario == "backward-ack" else first
    if scenario == "stale-epoch":
        frame["epoch"] = 6
    elif scenario == "future-ack":
        frame["sequence"] = 3
    elif scenario.startswith("stale-") and scenario != "stale-heartbeat":
        frame = {"type": scenario.removeprefix("stale-"), "epoch": 6}
        details: dict[str, dict[str, Any]] = {
            "heartbeat": {"nonce": 42},
            "resize": {"cols": 80, "rows": 24},
            "input": {"source": "user", "data": "late"},
        }
        frame.update(details[frame["type"]])
    elif scenario == "detach":
        frame = {"type": "detach", "epoch": 7}
    if scenario == "disconnect":
        controls.put_nowait({"type": "websocket.disconnect", "code": 1000})
    else:
        controls.put_nowait(
            {
                "type": "websocket.receive",
                "text": "{}" if scenario == "malformed" else json.dumps(frame),
            }
        )
    if scenario == "flood-stalled":
        await asyncio.wait_for(attempted.wait(), 1)
        for _ in range(20):
            controls.put_nowait({"type": "websocket.receive", "text": json.dumps(frame)})
    if scenario == "missing-status":
        await asyncio.wait_for(attempted.wait(), 1)
        release.set()
    try:
        async with asyncio.timeout(0.5):
            result = (await asyncio.gather(task, return_exceptions=True))[0]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cancelled.is_set()
    assert not any(
        '"type":"status"' in message.get("text", "").replace(" ", "") for message in messages
    )
    assert result is None or isinstance(result, BaseException)
    return result
