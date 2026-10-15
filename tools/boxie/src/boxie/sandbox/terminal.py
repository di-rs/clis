"""Agent-owned terminals outlive API connections; bytes are never decoded."""

import asyncio
from typing import TYPE_CHECKING

from fastapi import WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from boxie.domain.errors import BoxieError
from boxie.domain.models import SandboxState, TerminationReason
from boxie.sandbox.docker import TerminalPort
from boxie.sandbox.protocol import ResizeWire

if TYPE_CHECKING:
    from boxie.sandbox.registry import Agent, Record


class Terminal:
    def __init__(self, agent: Agent, record: Record, stream: TerminalPort) -> None:
        self.agent, self.record, self.stream = agent, record, stream
        self.output: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.reader: asyncio.Task[None] | None = None
        self.attached = False
        self.closed = False

    def start(self) -> None:
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        reason = TerminationReason.EXITED
        try:
            while data := await self.stream.read():
                remaining = self.record.snapshot.limits.output_bytes - self.record.output_bytes
                if data[:remaining]:
                    self.output.put_nowait(data[:remaining])
                    self.record.output_bytes += len(data[:remaining])
                if len(data) >= remaining:
                    reason = TerminationReason.OUTPUT_LIMIT
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await self.agent.remove(
            self.agent.config.identity, self.record.id, self.record.generation, reason
        )

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.reader and self.reader is not asyncio.current_task():
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        try:
            async with asyncio.timeout(self.agent.config.docker_timeout):
                await self.stream.close()
        finally:
            self.output.put_nowait(None)

    async def serve(self, websocket: WebSocket) -> None:
        if self.attached:
            raise BoxieError(409, "terminal is unavailable")
        self.agent.attach(self.record)
        self.attached = True
        await websocket.accept()

        async def send() -> None:
            while (data := await self.output.get()) is not None:
                for offset in range(0, len(data), 65536):
                    await websocket.send_bytes(data[offset : offset + 65536])
            await websocket.send_json({"type": "status", "reason": self.record.reason})
            await websocket.close()

        async def receive() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if self.record.state != SandboxState.READY:
                    return
                if (data := message.get("bytes")) is not None:
                    if not data or len(data) > 65536:
                        await websocket.close(code=1008)
                        return
                    async with asyncio.timeout(self.agent.config.docker_timeout):
                        await self.stream.write(data)
                    self.agent.accepted_input(self.record)
                elif (text := message.get("text")) is not None:
                    try:
                        if len(text) > 1024:
                            raise ValueError("oversized resize")
                        size = ResizeWire.model_validate_json(text)
                    except ValueError, ValidationError:
                        await websocket.close(code=1008)
                        return
                    async with asyncio.timeout(self.agent.config.docker_timeout):
                        await self.stream.resize(size.columns, size.rows)

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        except WebSocketDisconnect, RuntimeError, TimeoutError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.agent.disconnected(self.record)
