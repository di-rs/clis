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

    def __init__(
        self, app: ASGIApp, *, completed: Callable[[str, str, str, float], None] | None = None
    ) -> None:
        self.app = app
        self.completed = completed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        runtime = configured_runtime()
        status, finished, disconnected = "error", False, False
        failure: BaseException | None = None

        async def observed_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message: Message) -> None:
            nonlocal status, finished
            await send(message)
            if message["type"] == "http.response.start":
                code = message.get("status", 500)
                status = f"{code // 100}xx" if type(code) is int and 100 <= code < 600 else "error"
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True

        with bind_context(request_id=str(uuid4())):
            try:
                await self.app(scope, observed_receive, observed_send)
            except BaseException as error:
                failure = error
                status = "disconnected" if disconnected else "error"
                raise
            finally:
                if not finished:
                    status = "disconnected" if disconnected else "error"
                duration = time.monotonic() - started
                # A router's template is checked against an owning fixed catalog by
                # the completion sink. A raw path never enters an instrument.
                route = getattr(
                    scope.get("route"), "path", "unmatched" if status == "4xx" else "unresolved"
                )
                if self.completed is not None:
                    try:
                        self.completed(scope.get("method", "other"), route, status, duration)
                    except Exception:
                        pass
                if runtime is not None:
                    runtime.emit(
                        "request.completed",
                        level=40 if failure is not None else 20,
                        error=failure,
                        duration_ms=duration * 1000,
                        outcome="failed" if status == "error" else "succeeded",
                    )
