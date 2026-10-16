"""Framework-independent ASGI request context and bounded event emission."""

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from uuid import uuid4

from service_logging.context import bind_context
from service_logging.logging import configured_runtime

# ASGI's callable contract, without a runtime web-framework dependency.
type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestLogging:
    """One bounded request event; no client-supplied IDs, URL, headers or body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        runtime = configured_runtime()
        with bind_context(request_id=str(uuid4())):
            try:
                await self.app(scope, receive, send)
            except Exception as error:
                if runtime is not None:
                    runtime.emit("request_failed", level=40, error=error, outcome="failed")
                raise
            else:
                if runtime is not None:
                    runtime.emit(
                        "request_completed", duration_ms=(time.monotonic() - started) * 1000
                    )
