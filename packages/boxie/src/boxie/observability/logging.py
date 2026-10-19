"""Boxie-owned safe templates and process assembly."""

import logging
import os
from typing import TYPE_CHECKING

from boxie.observability.events import EVENTS
from boxie.observability.telemetry import logging as producer
from boxie.observability.telemetry.asgi import RequestLogging as RequestLogging
from boxie.observability.telemetry.logging import LogDispatcher as LogDispatcher
from boxie.observability.telemetry.logging import LogSettings as LogSettings

if TYPE_CHECKING:
    from temporalio.runtime import Runtime

_temporal_runtime: Runtime | None = None

# Exact trusted source templates; arguments never cross this boundary.
_ROUTES = {
    (
        "boxie.bootstrap.supervisor",
        "profile reload rejected; retaining approved profiles",
    ): "profile_reload_rejected",
    (
        "boxie.sandbox.execution.supervisor",
        "execution journal persistence failed; new execution disabled",
    ): "journal_failed",
    (
        "boxie.sandbox.execution.supervisor",
        "execution journal persistence recovered",
    ): "journal_recovered",
    (
        "boxie.sandbox.execution.supervisor",
        "sandbox reconciliation failed; retry pending: %s",
    ): "reconciliation_pending",
    ("boxie.temporal.dispatcher", "command delivery pending: %s"): "outbox_pending",
    ("boxie.temporal.dispatcher", "outbox persistence unavailable"): "outbox_unavailable",
}
_EVENTS = {
    **EVENTS,
    "profile_reload_rejected": "Profile reload rejected",
    "journal_failed": "Execution journal persistence failed",
    "journal_recovered": "Execution journal persistence recovered",
    "reconciliation_pending": "Sandbox reconciliation pending",
    "outbox_pending": "Outbox delivery pending",
    "outbox_unavailable": "Outbox persistence unavailable",
}


def configure_logging(settings: LogSettings) -> LogDispatcher:
    return producer.configure_logging(settings, events=_EVENTS, routes=_ROUTES)


def bootstrap_logging(settings: LogSettings) -> LogDispatcher:
    return producer.bootstrap_logging(settings, events=_EVENTS, routes=_ROUTES)


def process_logging(role: str) -> LogDispatcher:
    dispatcher = bootstrap_logging(
        LogSettings(service="boxie", role=role, release=os.environ.get("BOXIE_RELEASE", "unknown"))
    )
    global _temporal_runtime
    if _temporal_runtime is not None:
        return dispatcher
    # SDK adapter remains replay aware. Raw suffixes and full Info never enter logs.
    from temporalio import activity, workflow
    from temporalio.runtime import (
        LogForwardingConfig,
        LoggingConfig,
        PrometheusConfig,
        Runtime,
        TelemetryConfig,
    )

    activity.logger.activity_info_on_message = False
    activity.logger.full_activity_info_on_extra = False
    workflow.logger.workflow_info_on_message = False
    workflow.logger.full_workflow_info_on_extra = False
    logging_config = LoggingConfig(
        filter="ERROR",
        forwarding=LogForwardingConfig(
            logger=logging.getLogger("temporalio.core"), append_log_fields_to_message=False
        ),
    )
    from boxie.observability.telemetry.metrics import private_bind_host

    try:
        host = private_bind_host(os.environ.get("BOXIE_METRICS_HOST", "127.0.0.1"))
        _temporal_runtime = Runtime(
            telemetry=TelemetryConfig(
                logging=logging_config,
                metrics=PrometheusConfig(
                    bind_address=f"{host}:{9201 if role == 'api' else 9203}",
                    durations_as_seconds=True,
                    counters_total_suffix=True,
                    unit_suffix=True,
                ),
            )
        )
    except Exception:
        # The native Runtime has no public close. Own one successful fallback
        # until restart, rather than reconstructing successful runtimes on retries.
        dispatcher.configuration_error()
        _temporal_runtime = Runtime(telemetry=TelemetryConfig(logging=logging_config))
    Runtime.set_default(_temporal_runtime)
    return dispatcher
