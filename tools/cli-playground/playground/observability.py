"""Independent Playground assembly: only the domain-neutral producer is shared."""

from service_logging import logging as producer
from service_logging.asgi import RequestLogging as RequestLogging
from service_logging.context import bind_context as bind_context
from service_logging.logging import LogRuntime as LogRuntime
from service_logging.logging import LogSettings as LogSettings

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
