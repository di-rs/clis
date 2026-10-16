"""Boxie-owned safe templates and process assembly."""

import logging
import os
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from service_logging import logging as producer
from service_logging.context import bind_context
from service_logging.logging import LogRuntime as LogRuntime
from service_logging.logging import LogSettings as LogSettings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send
    from temporalio.runtime import Runtime

_temporal_runtime: Runtime | None = None

# Exact trusted source templates; arguments never cross this boundary.
_ROUTES = {
    (
        "boxie.bootstrap.agent",
        "profile reload rejected; retaining approved profiles",
    ): "profile_reload_rejected",
    (
        "boxie.sandbox.lifecycle",
        "execution journal persistence failed; new execution disabled",
    ): "journal_failed",
    ("boxie.sandbox.lifecycle", "execution journal persistence recovered"): "journal_recovered",
    (
        "boxie.sandbox.lifecycle",
        "sandbox reconciliation failed; retry pending: %s",
    ): "reconciliation_pending",
    ("boxie.temporal.dispatcher", "outbox delivery pending: %s"): "outbox_pending",
    ("boxie.temporal.dispatcher", "outbox persistence unavailable"): "outbox_unavailable",
}
_EVENTS = {
    "profile_reload_rejected": "Profile reload rejected",
    "journal_failed": "Execution journal persistence failed",
    "journal_recovered": "Execution journal persistence recovered",
    "reconciliation_pending": "Sandbox reconciliation pending",
    "outbox_pending": "Outbox delivery pending",
    "outbox_unavailable": "Outbox persistence unavailable",
}


def configure_logging(settings: LogSettings) -> LogRuntime:
    return producer.configure_logging(settings, events=_EVENTS, routes=_ROUTES)


def bootstrap_logging(settings: LogSettings) -> LogRuntime:
    return producer.bootstrap_logging(settings, events=_EVENTS, routes=_ROUTES)


def process_logging(role: str) -> LogRuntime:
    runtime = bootstrap_logging(
        LogSettings(service="boxie", role=role, release=os.environ.get("BOXIE_RELEASE", "unknown"))
    )
    if role == "agent":
        return runtime
    global _temporal_runtime
    if _temporal_runtime is not None:
        return runtime
    # SDK adapter remains replay aware. Raw suffixes and full Info never enter logs.
    from temporalio import activity, workflow
    from temporalio.runtime import LogForwardingConfig, LoggingConfig, Runtime, TelemetryConfig

    activity.logger.activity_info_on_message = False
    activity.logger.full_activity_info_on_extra = False
    workflow.logger.workflow_info_on_message = False
    workflow.logger.full_workflow_info_on_extra = False
    _temporal_runtime = Runtime(
        telemetry=TelemetryConfig(
            logging=LoggingConfig(
                filter="ERROR",
                forwarding=LogForwardingConfig(
                    logger=logging.getLogger("temporalio.core"),
                    append_log_fields_to_message=False,
                ),
            )
        )
    )
    Runtime.set_default(_temporal_runtime)
    return runtime


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
