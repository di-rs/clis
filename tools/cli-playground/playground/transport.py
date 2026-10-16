"""HTTP and WebSocket adapters for the playground service."""

import asyncio
import json
import logging
import re
from urllib.parse import urlsplit

from fastapi import (
    WebSocket,
)
from fastapi.responses import PlainTextResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from playground.boxie_client import (
    TerminalPort,
)
from playground.config import Settings
from playground.models import (
    TerminalInput,
    terminal_message,
)

logger = logging.getLogger(__name__)
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


async def bridge_terminal(websocket: WebSocket, session: TerminalPort) -> None:
    async def output() -> None:
        forwarded = 0
        while (data := await session.receive()) is not None:
            if isinstance(data, str):
                await websocket.send_json(
                    {"type": "status", "reason": data, "message": f"Session ended: {data}."}
                )
                return
            chunk = data[: MAX_TERMINAL_OUTPUT_BYTES - forwarded]
            if chunk:
                await websocket.send_bytes(chunk)
                forwarded += len(chunk)
            if len(data) > len(chunk):
                await websocket.send_json(
                    {
                        "type": "status",
                        "reason": "output_limit",
                        "message": "Session ended: output_limit.",
                    }
                )
                return
            # At exact equality, read the orchestrator's authoritative status next.
            # Any further binary bytes take the bounded defensive close above.

    async def inputs() -> None:
        while True:
            message = await websocket.receive_text()
            if len(message.encode("utf-8")) > 16384:
                raise ValueError("terminal message exceeds 16 KiB")
            data = terminal_message.validate_json(message)
            if isinstance(data, TerminalInput):
                await session.send(data.model_dump_json())
            else:
                await session.send(
                    json.dumps({"type": "resize", "columns": data.cols, "rows": data.rows})
                )

    tasks = [asyncio.create_task(output()), asyncio.create_task(inputs())]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
