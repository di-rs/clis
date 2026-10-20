"""Framework-independent ASGI request context and bounded event emission."""

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from uuid import uuid4

from boxie.observability.telemetry.context import parse_request_id, request_context
from boxie.observability.telemetry.logging import configured_dispatcher

# ASGI's callable contract, without a web-framework dependency.
type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestLogging:
    """One bounded request event with authenticated diagnostic correlation."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        completed: Callable[[str, str, str, float], None] | None = None,
        accept_incoming: Callable[[Scope], bool] | None = None,
    ) -> None:
        self.app = app
        self.completed = completed
        self.accept_incoming = accept_incoming

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        values = [
            value for key, value in scope.get("headers", ()) if key.lower() == b"x-request-id"
        ]
        incoming = (
            parse_request_id(values[0].decode("ascii", errors="replace"))
            if len(values) == 1
            else None
        )
        selected = (
            incoming
            if incoming and self.accept_incoming and self.accept_incoming(scope)
            else str(uuid4())
        )
        scope.setdefault("state", {})["request_id"] = selected
        started = time.monotonic()
        dispatcher = configured_dispatcher()
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
            if message["type"] in (
                "http.response.start",
                "websocket.accept",
                "websocket.http.response.start",
            ):
                message = dict(message)
                message["headers"] = [
                    (k, v) for k, v in message.get("headers", ()) if k.lower() != b"x-request-id"
                ] + [(b"x-request-id", selected.encode("ascii"))]
            await send(message)
            if message["type"] == "http.response.start":
                code = message.get("status", 500)
                status = f"{code // 100}xx" if type(code) is int and 100 <= code < 600 else "error"
            elif message["type"] in ("websocket.close", "websocket.accept"):
                status, finished = "2xx", True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True

        with request_context(selected):
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
                healthy_probe = (
                    route in ("/healthz", "/readyz")
                    and status == "2xx"
                    and failure is None
                    and not disconnected
                )
                if dispatcher is not None and not healthy_probe:
                    dispatcher.emit(
                        "request.completed",
                        level=40
                        if failure is not None or status in ("error", "4xx", "5xx")
                        else 20,
                        error=failure,
                        duration_ms=duration * 1000,
                        outcome=(
                            "cancelled"
                            if status == "disconnected"
                            else "failed"
                            if status in ("error", "4xx", "5xx")
                            else "succeeded"
                        ),
                    )
