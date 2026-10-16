"""Independent Playground assembly: only the domain-neutral producer is shared."""

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from service_logging import logging as producer
from service_logging.context import bind_context
from service_logging.logging import LogRuntime as LogRuntime
from service_logging.logging import LogSettings as LogSettings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

_ROUTES = {
    ("playground.service", "Sandbox stop remains pending"): "sandbox_stop_pending",
    ("playground.service", "Artifact retirement remains pending"): "artifact_retirement_pending",
    (
        "playground.service",
        "Sandbox admission recovery remains pending",
    ): "admission_recovery_pending",
    (
        "playground.service",
        "Artifact publication recovery deferred or abandoned",
    ): "publication_recovery_pending",
    ("playground.service", "Failed to expire playground environments"): "expiry_failed",
}
_EVENTS = {
    "sandbox_stop_pending": "Sandbox stop remains pending",
    "artifact_retirement_pending": "Artifact retirement remains pending",
    "admission_recovery_pending": "Sandbox admission recovery remains pending",
    "publication_recovery_pending": "Artifact publication recovery deferred or abandoned",
    "expiry_failed": "Failed to expire playground environments",
}


def configure_logging(settings: LogSettings) -> LogRuntime:
    return producer.configure_logging(settings, events=_EVENTS, routes=_ROUTES)


def bootstrap_logging(settings: LogSettings) -> LogRuntime:
    return producer.bootstrap_logging(settings, events=_EVENTS, routes=_ROUTES)


class RequestLogging:
    """One bounded request event; no client-supplied IDs, URL, headers or body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        runtime = producer.configured_runtime()
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
