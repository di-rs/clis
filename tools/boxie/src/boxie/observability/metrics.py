"""Bounded process instruments and coherent immutable authoritative snapshots."""

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily, Metric

from boxie.domain.models import SandboxState
from boxie.observability.events import Transition, emit
from boxie.observability.telemetry.logging import LogRuntime
from boxie.observability.telemetry.metrics import ProcessMetrics

STATES = tuple(state.value for state in SandboxState)
KINDS = ("start", "stop")
API_DEPENDENCIES = (
    "postgres",
    "orchestrator",
    "temporal",
    "namespace",
    "workflow_poller",
    "activity_poller",
    "maintenance_workflow_poller",
    "maintenance_activity_poller",
)
ORCHESTRATOR_DEPENDENCIES = ("postgres", "docker", "execution")
ROUTES = (
    "/healthz",
    "/readyz",
    "/v1/sandboxes",
    "/v1/sandboxes/{sandbox_id}",
    "/v1/artifacts/{artifact_id}",
    "/v1/identity",
    "/v1/profiles/resolve",
)


@dataclass(frozen=True)
class OperationalSnapshot:
    timestamp: float
    states: tuple[int, ...]
    reserved: int
    limit: int
    cleanup_pending: int
    cleanup_age: float
    cleanup_unknown: int
    outbox: tuple[tuple[int, float], ...]


class Metrics(ProcessMetrics):
    def __init__(
        self,
        role: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        logs: LogRuntime | None = None,
    ) -> None:
        self.role, self.clock = role, clock
        self._lock = threading.Lock()
        self._snapshot: tuple[OperationalSnapshot, float] | None = None
        self._refresh_success = False
        self._dependencies: dict[str, tuple[bool, float]] = {}
        self.dependencies = (
            API_DEPENDENCIES
            if role == "api"
            else ORCHESTRATOR_DEPENDENCIES
            if role == "orchestrator"
            else ()
        )
        self._lease: tuple[int, float | None, float] | None = None
        super().__init__("boxie", routes=ROUTES, logs=logs)
        self.provisioning = Counter(
            "boxie_provisioning_total",
            "Observed committed provisioning outcomes; process lifetime, possible crash-window loss",
            ("outcome",),
            registry=self.registry,
        )
        for outcome in ("ready", "failed", "stopped"):
            self.provisioning.labels(outcome)
        self.provisioning_duration = Histogram(
            "boxie_provisioning_ready_observation_duration_seconds",
            "Admission to first SQL-observed readiness evidence; successful outcomes only",
            buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900, 3600),
            registry=self.registry,
        )

    def transition(self, event: Transition) -> None:
        if event.event == "provisioning.completed" and event.outcome in (
            "ready",
            "failed",
            "stopped",
        ):
            self.provisioning.labels(event.outcome).inc()
            if event.outcome == "ready" and event.duration_seconds is not None:
                self.provisioning_duration.observe(max(0, event.duration_seconds))
        emit(event)

    def refresh(self, snapshot: OperationalSnapshot) -> None:
        with self._lock:
            self._snapshot = (snapshot, self.clock())
            self._refresh_success = True

    def refresh_failed(self) -> None:
        with self._lock:
            self._refresh_success = False

    def dependency(self, name: str, ready: bool) -> None:
        if name not in self.dependencies:
            raise ValueError("unknown dependency")
        with self._lock:
            self._dependencies[name] = (ready, self.clock())

    def refresh_lease(self, active: int, remaining: float | None) -> None:
        with self._lock:
            self._lease = (active, remaining, self.clock())

    def collect(self) -> Iterable[Metric]:
        yield from super().collect()
        with self._lock:
            snapshot, success, dependencies, lease = (
                self._snapshot,
                self._refresh_success,
                self._dependencies.copy(),
                self._lease,
            )
        now = self.clock()
        known_family = GaugeMetricFamily(
            "boxie_dependency_known", "Fresh dependency evidence", labels=["dependency"]
        )
        ready_family = GaugeMetricFamily(
            "boxie_dependency_ready", "Dependency ready when evidence known", labels=["dependency"]
        )
        for name in self.dependencies:
            observation = dependencies.get(name)
            known = observation is not None and now - observation[1] <= 30
            known_family.add_metric([name], int(known))
            if known and observation is not None:
                ready_family.add_metric([name], int(observation[0]))
        if self.dependencies:
            yield known_family
            yield ready_family
        if self.role == "orchestrator" and lease is not None and now - lease[2] <= 5:
            yield GaugeMetricFamily(
                "boxie_orchestrator_control_lease_active",
                "Current boot eligible executions",
                value=lease[0],
            )
            if lease[1] is not None:
                yield GaugeMetricFamily(
                    "boxie_orchestrator_control_lease_remaining_seconds",
                    "Minimum remaining monotonic control lease",
                    value=max(0, lease[1] - max(0, now - lease[2])),
                )
        if self.role != "api":
            return
        age = max(0, now - snapshot[1]) if snapshot else 0
        fresh = snapshot is not None and age <= 30
        for suffix, value in (
            ("initialized", snapshot is not None),
            ("fresh", fresh),
            ("refresh_success", success),
        ):
            yield GaugeMetricFamily(
                "boxie_snapshot_" + suffix, "Authoritative snapshot health", value=int(value)
            )
        if snapshot is None:
            return
        sample = snapshot[0]
        yield GaugeMetricFamily(
            "boxie_snapshot_age_seconds", "Time since last successful snapshot", value=age
        )
        yield GaugeMetricFamily(
            "boxie_snapshot_last_success_timestamp_seconds",
            "Database sample timestamp",
            value=sample.timestamp,
        )
        if not fresh:
            return
        states = GaugeMetricFamily(
            "boxie_sandboxes", "Retained authoritative sandboxes", labels=["state"]
        )
        for name, count in zip(STATES, sample.states, strict=True):
            states.add_metric([name], count)
        yield states
        for name, amount in (
            ("capacity_reserved", sample.reserved),
            ("capacity_limit", sample.limit),
            ("cleanup_pending", sample.cleanup_pending),
            ("cleanup_age_unknown", sample.cleanup_unknown),
        ):
            yield GaugeMetricFamily("boxie_" + name, "Authoritative SQL aggregate", value=amount)
        if not sample.cleanup_unknown:
            yield GaugeMetricFamily(
                "boxie_cleanup_oldest_age_seconds",
                "Oldest SQL-known pending cleanup demand",
                value=sample.cleanup_age + age if sample.cleanup_pending else 0,
            )
        pending = GaugeMetricFamily(
            "boxie_outbox_pending",
            "Undelivered outbox entries including leased retries",
            labels=["kind"],
        )
        oldest = GaugeMetricFamily(
            "boxie_outbox_oldest_age_seconds", "Oldest undelivered outbox entry", labels=["kind"]
        )
        for name, (count, seconds) in zip(KINDS, sample.outbox, strict=True):
            pending.add_metric([name], count)
            oldest.add_metric([name], seconds + age if count else 0)
        yield pending
        yield oldest
