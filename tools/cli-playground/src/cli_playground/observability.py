"""Consumer-owned request context and bounded, sanitized process diagnostics."""

import atexit
import json
import logging
import os
import queue
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request: ContextVar[str | None] = ContextVar("request_id", default=None)
_sandbox: ContextVar[str | None] = ContextVar("sandbox_id", default=None)
_EVENTS = {
    "Request completed": ("request.completed", "Request completed"),
    "Sandbox stop remains pending": ("sandbox.stop_pending", "Sandbox stop remains pending"),
    "Artifact retirement remains pending": (
        "artifact.retirement_pending",
        "Artifact retirement remains pending",
    ),
    "Sandbox admission recovery remains pending": (
        "sandbox.admission_pending",
        "Sandbox admission recovery remains pending",
    ),
    "Artifact publication recovery deferred or abandoned": (
        "artifact.publication_pending",
        "Artifact publication remains pending",
    ),
    "Failed to expire playground environments": ("cleanup.failed", "Environment cleanup failed"),
}


def current_request_id() -> str | None:
    return _request.get()


def _canonical(value: str | None) -> str | None:
    try:
        return (
            value if value is not None and len(value) == 36 and str(UUID(value)) == value else None
        )
    except ValueError:
        return None


@contextmanager
def request_context(request_id: str | None) -> Iterator[None]:
    token = _request.set(_canonical(request_id))
    try:
        yield
    finally:
        _request.reset(token)


@contextmanager
def sandbox_context(sandbox_id: UUID) -> Iterator[None]:
    token = _sandbox.set(str(sandbox_id))
    try:
        yield
    finally:
        _sandbox.reset(token)


class RequestContext:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        selected = str(uuid4())
        scope.setdefault("state", {})["request_id"] = selected
        failed = False

        async def respond(message: Message) -> None:
            nonlocal failed
            if message["type"] in {"http.response.start", "websocket.http.response.start"}:
                failed = message["status"] >= 400
            if message["type"] in {
                "http.response.start",
                "websocket.accept",
                "websocket.http.response.start",
            }:
                message = dict(message)
                message["headers"] = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ] + [(b"x-request-id", selected.encode())]
            await send(message)

        with request_context(selected):
            try:
                await self.app(scope, receive, respond)
            except BaseException:
                failed = True
                raise
            finally:
                if failed or scope.get("path") != "/healthz":
                    logging.getLogger(__name__).log(
                        logging.ERROR if failed else logging.INFO, "Request completed"
                    )


async def unhandled_error(request: Request, _error: Exception) -> PlainTextResponse:
    return PlainTextResponse(
        "Internal Server Error", status_code=500, headers={"X-Request-ID": request.state.request_id}
    )


class _Handler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: queue.Queue[bytes] = queue.Queue(maxsize=2047)
        self.instance_id = str(uuid4())
        self.closed = False
        try:
            self.sink: int | None = os.dup(sys.stdout.fileno())
        except OSError, ValueError, AttributeError:
            self.sink = None
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self.closed or self.sink is None:
            return
        event, message = ("foreign_log", "Dependency diagnostic")
        if (
            type(record.name) is str
            and record.name.startswith("cli_playground.")
            and type(record.msg) is str
            and len(record.msg) <= 256
        ):
            event, message = _EVENTS.get(record.msg, (event, message))
        data = {
            "schema_version": 1,
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": {10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"}.get(
                record.levelno, "ERROR"
            ),
            "event": event,
            "message": message,
            "service": "cli-playground",
            "role": "api",
            "instance_id": self.instance_id,
        }
        if identity := current_request_id():
            data["request_id"] = identity
        if sandbox := _sandbox.get():
            data["sandbox_id"] = sandbox
        line = (json.dumps(data, separators=(",", ":")) + "\n").encode()
        if len(line) <= 8192:
            try:
                self.records.put_nowait(line)
            except queue.Full, queue.ShutDown:
                pass

    def _write(self) -> None:
        try:
            while True:
                line = self.records.get()
                if self.sink is not None:
                    while line:
                        count = os.write(self.sink, line)
                        if count <= 0:
                            return
                        line = line[count:]
        except queue.ShutDown, OSError:
            pass
        finally:
            if self.sink is not None:
                os.close(self.sink)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.records.shutdown()
        if threading.current_thread() is not self.writer:
            self.writer.join(timeout=2)
        super().close()


_handler: _Handler | None = None


def configure_logging() -> None:
    global _handler
    if _handler is None or _handler.closed:
        _handler = _Handler()
    root = logging.getLogger()
    root.handlers[:] = [_handler]
    root.setLevel(logging.INFO)
    for value in logging.Logger.manager.loggerDict.values():
        if isinstance(value, logging.Logger):
            value.handlers.clear()
            value.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


def shutdown_logging() -> None:
    if _handler is not None:
        _handler.close()


atexit.register(shutdown_logging)
