"""HTTP and WebSocket adapters for the playground service."""

import asyncio
import re
from collections.abc import Callable
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import (
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import PlainTextResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from cli_playground.boxie_client import (
    TerminalPort,
    TerminalWriteClosed,
)
from cli_playground.config import Settings
from cli_playground.terminal_protocol import (
    Ack,
    Attached,
    AttachRequest,
    Heartbeat,
    Input,
    Resize,
    TerminalStatus,
    control,
)

MAX_TERMINAL_OUTPUT_BYTES = 16 * 1024 * 1024


class RequestHostBoundary:
    """Accept only configured authorities; proxy headers never expand this boundary."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        hosts = Headers(scope=scope).getlist("host")
        status = 400
        valid = len(hosts) == 1 and self.settings.accepts_host(hosts[0])
        if valid and hosts[0] != urlsplit(self.settings.public_url).netloc:
            match = re.fullmatch(
                r"/(?:p|ws|api/environments)/([^/]+)(?:/sessions(?:/[0-9a-f-]+)?)?", scope["path"]
            )
            if match is not None:
                valid = self.settings.accepts_host(hosts[0], match[1])
            elif scope["path"].startswith("/api/"):
                valid = False
            if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD"}:
                valid = bool(
                    valid
                    and match
                    and (
                        (scope["method"] == "POST" and scope["path"].endswith("/sessions"))
                        or (
                            scope["method"] == "DELETE"
                            and re.fullmatch(
                                r"/api/environments/[^/]+/sessions/[0-9a-f-]+", scope["path"]
                            )
                        )
                    )
                )
            status = 404
        if not valid:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = PlainTextResponse("Invalid preview host", status_code=status)
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


TERMINAL_SEND_TIMEOUT = 10.0
TERMINAL_CLOSE_TIMEOUT = 2.0
TERMINAL_DRAIN_TIMEOUT = 2.0


async def close_terminal(websocket: WebSocket, code: int = 1000) -> None:
    with suppress(WebSocketDisconnect, RuntimeError, TimeoutError):
        async with asyncio.timeout(TERMINAL_CLOSE_TIMEOUT):
            await websocket.close(code=code)


async def bridge_terminal(
    websocket: WebSocket,
    session: TerminalPort,
    attached: Callable[[], None] | None = None,
    prepare_attach: Callable[[AttachRequest], str] | None = None,
) -> None:
    epoch: int | None = None
    highest = acked = 0
    last_nonce = -1
    write_closed: TerminalWriteClosed | None = None

    async def output() -> None:
        nonlocal epoch, highest
        forwarded = 0
        while (data := await session.receive()) is not None:
            async with asyncio.timeout(TERMINAL_SEND_TIMEOUT):
                if not isinstance(data, bytes):
                    message = data
                    if isinstance(message, Attached):
                        epoch = message.epoch
                        if attached is not None:
                            attached()
                    value = message.model_dump()
                    if isinstance(message, TerminalStatus):
                        value["message"] = f"Session ended: {message.reason}."
                    await websocket.send_json(value)
                    if isinstance(message, TerminalStatus):
                        return
                    continue
                if len(data) < 9:
                    raise ValueError("invalid terminal output")
                sequence = int.from_bytes(data[:8], "big")
                payload = data[8:]
                if sequence <= highest:
                    continue
                highest = sequence
                chunk = payload[: MAX_TERMINAL_OUTPUT_BYTES - forwarded]
                if chunk:
                    await websocket.send_bytes(data[:8] + chunk)
                    forwarded += len(chunk)
                if len(chunk) < len(payload):
                    await websocket.send_json(
                        {
                            "type": "status",
                            "reason": "output_limit",
                            "message": "Session ended: output_limit.",
                        }
                    )
                    return

        if write_closed is not None:
            raise write_closed

    async def inputs() -> None:
        nonlocal highest, acked, last_nonce, write_closed
        while True:
            message = await websocket.receive_text()
            if len(message.encode("utf-8")) > 16384:
                raise ValueError("terminal message exceeds 16 KiB")
            data = control.validate_json(message)
            if isinstance(data, AttachRequest):
                if prepare_attach is None:
                    raise ValueError("attachment ordering is required")
                wire = prepare_attach(data)
                highest = acked = data.resume_after
            else:
                wire = data.model_dump_json()
            try:
                async with asyncio.timeout(TERMINAL_SEND_TIMEOUT):
                    await session.send(wire)
            except TerminalWriteClosed as error:
                if not (
                    isinstance(data, (Ack, Heartbeat, Input, Resize))
                    and data.epoch == epoch
                    and (not isinstance(data, Ack) or acked <= data.sequence <= highest)
                    and (not isinstance(data, Heartbeat) or data.nonce > last_nonce)
                ):
                    raise
                write_closed = error
                # Preserve final output when a valid late control races the close.
                # EOF without a terminal status is still an upstream failure.
                async with asyncio.timeout(TERMINAL_DRAIN_TIMEOUT):
                    await asyncio.shield(output_task)
                return
            if isinstance(data, Ack):
                acked = data.sequence
            elif isinstance(data, Heartbeat):
                last_nonce = data.nonce

    output_task = asyncio.create_task(output())
    tasks = [output_task, asyncio.create_task(inputs())]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        async with asyncio.timeout(TERMINAL_CLOSE_TIMEOUT):
            await asyncio.gather(*tasks, return_exceptions=True)
