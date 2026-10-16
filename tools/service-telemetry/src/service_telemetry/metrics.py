"""Domain-neutral HTTP/log metrics and an owned, fail-open private listener."""

import ipaddress
import threading
import time
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from service_telemetry.logging import LogRuntime

METHODS = frozenset(("GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "other"))
STATUS = frozenset(("1xx", "2xx", "3xx", "4xx", "5xx", "disconnected", "error"))


class ProcessMetrics:
    def __init__(
        self, prefix: str, *, routes: Iterable[str] = (), logs: LogRuntime | None = None
    ) -> None:
        self.prefix, self.logs = prefix, logs
        self.routes = frozenset(routes) | {"unmatched", "unresolved"}
        self.registry = CollectorRegistry()
        self.requests = Counter(
            prefix + "_http_requests_total",
            "Completed HTTP requests",
            ("method", "route", "status_class"),
            registry=self.registry,
        )
        self.request_duration = Histogram(
            prefix + "_http_request_duration_seconds",
            "HTTP request completion duration",
            ("method", "route", "status_class"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
            registry=self.registry,
        )
        self.registry.register(self)

    def http_completed(self, method: str, route: str, status: str, duration: float) -> None:
        labels = (
            method if method in METHODS else "other",
            route if route in self.routes else "unresolved",
            status if status in STATUS else "error",
        )
        self.requests.labels(*labels).inc()
        self.request_duration.labels(*labels).observe(max(0, duration))

    def collect(self) -> Iterable[Metric]:
        if self.logs is not None:
            stats = self.logs.stats
            yield GaugeMetricFamily(
                self.prefix + "_log_queue_occupancy",
                "Queued and pending log records",
                value=stats.queued_records,
            )
            yield GaugeMetricFamily(
                self.prefix + "_log_sink_available",
                "Log sink enabled",
                value=int(stats.sink_state == "enabled"),
            )
            yield CounterMetricFamily(
                self.prefix + "_log_dropped", "Dropped log events", value=stats.dropped_count
            )

    def render(self) -> bytes:
        return generate_latest(self.registry)


def private_bind_host(host: str) -> str:
    address = ipaddress.ip_address(host)
    if address.version != 4 or not (
        address.is_loopback or address.is_private or address.is_unspecified
    ):
        raise ValueError("metrics bind must be private IPv4")
    return host


class MetricsListener:
    """One daemon owner; no network work in the application/event loop."""

    def __init__(
        self, metrics: ProcessMetrics, host: str, port: int, *, retry_seconds: float = 15
    ) -> None:
        # Explicit container 0.0.0.0 is supported; production must not publish it.
        private_bind_host(host)
        if not 0 <= port <= 65535:
            raise ValueError("invalid IPv4 metrics listener")
        self.metrics, self.host, self.port = metrics, host, port
        self.retry_seconds = retry_seconds
        self._stop = threading.Event()
        self._close_lock = threading.Lock()
        self._close_deadline: float | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="private-metrics")
        self._thread.start()

    def _run(self) -> None:
        render = self.metrics.render

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/metrics":
                    self.send_error(404)
                    return
                body = render()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        while not self._stop.is_set():
            try:
                server = ThreadingHTTPServer((self.host, self.port), Handler)
            except OSError:
                self._stop.wait(self.retry_seconds)
                continue
            server.daemon_threads = True
            server.timeout = 0.1
            self._server = server
            try:
                while not self._stop.is_set():
                    server.handle_request()
            finally:
                server.server_close()
                self._server = None
            return

    @property
    def address(self) -> tuple[str, int] | None:
        server = self._server
        return (str(server.server_address[0]), int(server.server_address[1])) if server else None

    def close(self, timeout: float = 2) -> None:
        with self._close_lock:
            if self._close_deadline is None:
                self._close_deadline = time.monotonic() + min(2.0, max(0.0, timeout))
            deadline = self._close_deadline
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
