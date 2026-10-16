"""Fenced execution of durable admissions; PostgreSQL owns all reservations."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from boxie.bootstrap.config import OrchestratorConfig
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import (
    EnsureRequest,
    ExpiryDecision,
    ExpiryRequest,
    OrchestratorObservation,
    SandboxRef,
    StartGuard,
)
from boxie.domain.models import Principal, SandboxState, TerminationReason
from boxie.domain.policy import validate_policy
from boxie.sandbox.docker import ExecutionDocker
from boxie.sandbox.execution import ExecutionJournal, ExecutionRecord
from boxie.sandbox.profiles import ProfileRegistry
from boxie.sandbox.provision import ProvisionDecisions, provision
from boxie.sandbox.reconcile import reconcile_removal
from boxie.sandbox.retention import MAX_RECORDS, compact
from boxie.sandbox.terminal import ExecutionTerminal

logger = logging.getLogger(__name__)

FINAL = {SandboxState.TERMINATED, SandboxState.FAILED}
ArtifactPath = Callable[[Principal, UUID], Awaitable[Path]]


@dataclass
class Deadlines:
    absolute: float
    lease: float
    idle: float | None = None
    disconnect: float | None = None


class DurableOrchestrator:
    def __init__(
        self,
        config: OrchestratorConfig,
        docker: ExecutionDocker,
        artifact_path: ArtifactPath,
        guard: StartGuard,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
    ) -> None:
        self.config, self.docker, self.artifact_path, self.guard = (
            config,
            docker,
            artifact_path,
            guard,
        )
        self.clock, self.monotonic, self.boot_id = clock, monotonic, uuid4()
        self.profiles = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
        self.journal = ExecutionJournal(config.data_dir / "execution.json", config.identity)
        self.records = self.journal.records
        self.admission_lock = asyncio.Lock()
        self.sessions: dict[UUID, int] = {}
        self.durable_versions = {key: value.event_version for key, value in self.records.items()}
        self.forget_observation: Callable[[UUID], None] | None = None
        self._input_flush_at: float | None = None
        self.deadlines: dict[UUID, Deadlines] = {}
        self.locks: dict[UUID, asyncio.Lock] = {}
        self.cleanup_locks: dict[UUID, asyncio.Lock] = {}
        self.workers: dict[UUID, asyncio.Task[None]] = {}
        self.creates: dict[UUID, asyncio.Task[None]] = {}
        self.terminals: dict[UUID, ExecutionTerminal] = {}
        self.terminal_readers: dict[UUID, asyncio.Task[None]] = {}
        self.reconciliations: dict[UUID, asyncio.Task[None]] = {}
        self.sweeper: asyncio.Task[None] | None = None
        self.compactor: asyncio.Task[None] | None = None
        self.stopping = False
        self.observer: Callable[[OrchestratorObservation], None] | None = None
        self._reported: dict[UUID, int] = {}
        self._journal_healthy = True
        self._failed_reconciliations: set[UUID] = set()

    def _record(self, ref: SandboxRef) -> ExecutionRecord:
        record = self.records.get(ref.id)
        if record is None or record.generation != ref.generation:
            raise BoxieError(404, "sandbox generation not found")
        return record

    def _lock(self, ref: SandboxRef) -> asyncio.Lock:
        self._record(ref)
        return self.locks.setdefault(ref.id, asyncio.Lock())

    @property
    def execution_ready(self) -> bool:
        """Local execution readiness, not Docker/SQL/control-plane availability.

        False after an observed journal or reconciliation failure, until a full
        journal flush and successful per-record retries establish recovery.
        Task 3 must include this signal in its authenticated readiness response.
        """
        return (
            not self.stopping
            and self._journal_healthy
            and not self._failed_reconciliations
            and len(self.records) < MAX_RECORDS
        )

    def _save(self) -> None:
        try:
            self.journal.save()
        except Exception:
            if self._journal_healthy:
                logger.error("execution journal persistence failed; new execution disabled")
            self._journal_healthy = False
            raise
        if not self._journal_healthy:
            logger.info("execution journal persistence recovered")
        self._journal_healthy = True
        self._input_flush_at = None
        self.durable_versions = {key: value.event_version for key, value in self.records.items()}
        if self.observer:
            for record in self.records.values():
                if self._reported.get(record.id) != record.event_version:
                    self.observer(self._observation(record))
                    self._reported[record.id] = record.event_version

    def _changed(self, record: ExecutionRecord) -> None:
        record.event_version += 1
        self._save()

    def _observation(self, record: ExecutionRecord) -> OrchestratorObservation:
        return OrchestratorObservation(
            record.id,
            Principal(record.consumer_id),
            self.config.identity,
            record.generation,
            self.boot_id,
            record.event_version,
            self.clock(),
            record.state,
            record.container_id is not None,
            record.removal_confirmed,
            record.cleanup_pending,
            record.reason,
            record.ready_at,
            record.last_input_at,
            record.disconnected_at,
            record.attachment_epoch,
            record.absolute_deadline,
            record.control_lease_until,
            record.container_id,
        )

    async def inspect(self, ref: SandboxRef) -> OrchestratorObservation:
        async with self._lock(ref):
            return self._observation(self._record(ref))

    async def ensure(self, request: EnsureRequest) -> OrchestratorObservation:
        if request.identity != self.config.identity:
            raise BoxieError(404, "orchestrator identity not found")
        validate_policy(request.spec.policy, self.config.policy_maximum)
        self.profiles.validate_snapshot(request.profile_snapshot)
        snapshot, spec = request.profile_snapshot, request.spec
        if (snapshot.profile_id, snapshot.command, snapshot.env) != (
            spec.profile_id,
            spec.command,
            spec.env,
        ):
            raise BoxieError(422, "snapshot and request differ")
        if (
            request.accepted_at.tzinfo is None
            or request.absolute_deadline.tzinfo is None
            or request.absolute_deadline
            != request.accepted_at + timedelta(seconds=spec.policy.max_seconds)
        ):
            raise BoxieError(422, "invalid admitted deadline")
        async with self.admission_lock:
            old = self.records.get(request.ref.id)
            if old:
                if old.request() != request:
                    raise BoxieError(409, "immutable execution request differs")
                return self._observation(old)
            mode = await self.guard.initialization(request, self.boot_id)
            if mode is None:
                raise BoxieError(409, "durable admission unavailable")
            # Existing cleanup keeps progressing; only new identities consume headroom.
            limit = MAX_RECORDS if mode == "execute" else MAX_RECORDS + 8
            if (
                self.stopping
                or not self._journal_healthy
                or (mode == "execute" and self._failed_reconciliations)
                or len(self.records) >= limit
            ):
                raise BoxieError(503, "orchestrator execution unavailable")
            now = self.clock()
            record = ExecutionRecord.create(request, self.boot_id, now + timedelta(seconds=90))
            if mode != "execute":
                record.state = SandboxState.TERMINATING
                record.cleanup_pending = True
                record.reason = mode
            self.records[record.id] = record
            self.deadlines[record.id] = Deadlines(
                self.monotonic() + max(0, (request.absolute_deadline - now).total_seconds()),
                self.monotonic() + 90,
            )
            try:
                self._save()
            except Exception:
                # The atomic replacement may have succeeded before fsync failed.
                # Keep the identity fenced and retry cleanup, never start this request.
                record.state = SandboxState.TERMINATING
                record.cleanup_pending = True
                record.reason = TerminationReason.PROVISIONING_FAILED
                record.event_version += 1
                raise BoxieError(503, "orchestrator execution unavailable") from None
            self.workers[record.id] = asyncio.create_task(
                self._provision(record) if mode == "execute" else self._cleanup(record)
            )
            return self._observation(record)

    def _expiry(self, record: ExecutionRecord) -> TerminationReason | None:
        timers = self.deadlines.get(record.id)
        if timers is None:
            return TerminationReason.ORCHESTRATOR_RESTART
        choices = [
            (timers.absolute, TerminationReason.EXPIRED),
            (timers.lease, TerminationReason.CONTROL_PLANE_LOST),
        ]
        if timers.idle is not None:
            choices.append((timers.idle, TerminationReason.IDLE))
        if timers.disconnect is not None:
            choices.append((timers.disconnect, TerminationReason.DISCONNECTED))
        deadline, reason = min(choices, key=lambda item: item[0])
        return reason if self.monotonic() >= deadline else None

    def _live(self, record: ExecutionRecord) -> bool:
        return (
            not self.stopping
            and self._journal_healthy
            and not self._failed_reconciliations
            and record.boot_id == self.boot_id
            and record.state not in FINAL | {SandboxState.TERMINATING}
            and self._expiry(record) is None
        )

    async def _provision(self, record: ExecutionRecord) -> None:
        await provision(
            record,
            self.docker,
            self.artifact_path,
            self.guard,
            self.boot_id,
            self.config.docker_timeout,
            ProvisionDecisions(
                self._lock,
                self._live,
                self._expiry,
                self._changed,
                self.remove,
                self._cleanup,
                self._ready,
                self.creates.__setitem__,
                self.terminals.__setitem__,
            ),
        )

    async def _ready(self, record: ExecutionRecord) -> bool:
        async with self._lock(record.ref):
            if not self._live(record):
                return False
            now = self.clock()
            record.state, record.ready_at, record.disconnected_at = SandboxState.READY, now, now
            timers = self.deadlines[record.id]
            timers.idle = self.monotonic() + record.spec.policy.idle_seconds
            timers.disconnect = self.monotonic() + record.spec.policy.disconnect_seconds
            self._changed(record)
            return True

    def _stop(self, record: ExecutionRecord, reason: TerminationReason) -> None:
        if record.state in FINAL:
            return
        record.state, record.cleanup_pending = SandboxState.TERMINATING, True
        record.reason = record.reason or reason
        record.attached = False
        try:
            self._changed(record)
        except Exception:
            # A full disk must not defeat expiry. The prior execution/create intent
            # is already durable; retain the in-memory stop latch and attempt only
            # ownership-checked removal. Confirmation still requires a final save.
            pass

    async def remove(self, ref: SandboxRef, reason: TerminationReason) -> OrchestratorObservation:
        async with self._lock(ref):
            record = self._record(ref)
            self._stop(record, reason)
        await self._cleanup(record)
        return await self.inspect(ref)

    async def _cleanup(self, record: ExecutionRecord) -> None:
        async with self.cleanup_locks.setdefault(record.id, asyncio.Lock()):
            if record.state in FINAL:
                return
            terminal = self.terminals.pop(record.id, None)
            if terminal:
                if terminal.reader:
                    self.terminal_readers[record.id] = terminal.reader
                try:
                    async with asyncio.timeout(self.config.docker_timeout):
                        await terminal.close()
                except Exception:
                    pass
            create = self.creates.get(record.id)
            try:
                await reconcile_removal(
                    self.docker,
                    record,
                    self._save,
                    self.config.docker_timeout,
                    create is not None and not create.done(),
                )
            except Exception:
                if self._journal_healthy:
                    raise
                # Physical deletion may have succeeded, but the failed final save
                # leaves a pending, unconfirmed record for autonomous retry.

    async def expire_if_current(self, request: ExpiryRequest) -> ExpiryDecision:
        async with self._lock(request.ref):
            record = self._record(request.ref)
            unconditional = request.reason in {
                TerminationReason.EXPIRED,
                TerminationReason.REQUESTED,
            }
            if record.state in FINAL or (
                not unconditional
                and (
                    request.boot_id != self.boot_id
                    or request.observed_event_version != record.event_version
                    or self._expiry(record) != request.reason
                )
            ):
                return ExpiryDecision(False, self._observation(record))
            self._stop(record, request.reason)
        await self._cleanup(record)
        return ExpiryDecision(True, await self.inspect(request.ref))

    async def renew_control_lease(self, ref: SandboxRef, boot_id: UUID) -> OrchestratorObservation:
        async with self._lock(ref):
            record = self._record(ref)
            if boot_id != self.boot_id or not self._live(record):
                raise BoxieError(409, "control lease fence expired")
            self.deadlines[ref.id].lease = self.monotonic() + 90
            record.control_lease_until = self.clock() + timedelta(seconds=90)
            self._changed(record)
            return self._observation(record)

    def _attachment(self, record: ExecutionRecord, epoch: int | None = None) -> None:
        if (
            record.state != SandboxState.READY
            or not self._live(record)
            or (epoch is not None and (epoch != record.attachment_epoch or not record.attached))
        ):
            raise BoxieError(409, "terminal attachment is unavailable")

    async def attach(
        self, ref: SandboxRef, *, attachment_order: int, resume_after: int | None = None
    ) -> int:
        # Transport calls this only AFTER websocket.accept succeeds.
        async with self._lock(ref):
            record = self._record(ref)
            self._attachment(record)
            if not record.attachment_order < attachment_order <= 2**53 - 1:
                raise BoxieError(409, "terminal attachment order superseded")
            if resume_after is not None:
                self.terminals[ref.id].replay.after(resume_after)
            record.attachment_order = attachment_order
            record.attachment_epoch += 1
            record.attached, record.disconnected_at = True, None
            self.deadlines[ref.id].disconnect = None
            self._changed(record)
            return record.attachment_epoch

    async def check_attachment(
        self, ref: SandboxRef, epoch: int, *, allow_ended: bool = False
    ) -> None:
        async with self._lock(ref):
            record = self._record(ref)
            if epoch != record.attachment_epoch:
                raise BoxieError(409, "terminal attachment superseded")
            if not allow_ended or record.state == SandboxState.READY:
                self._attachment(record, epoch)

    async def detach(self, ref: SandboxRef, epoch: int) -> None:
        async with self._lock(ref):
            record = self._record(ref)
            if (
                record.state == SandboxState.READY
                and epoch == record.attachment_epoch
                and record.attached
            ):
                record.attached = False
                record.disconnected_at = self.clock()
                self.deadlines[ref.id].disconnect = (
                    self.monotonic() + record.spec.policy.disconnect_seconds
                )
                self._changed(record)

    async def write(
        self,
        ref: SandboxRef,
        epoch: int,
        data: bytes,
        *,
        source: Literal["user", "terminal"] = "user",
    ) -> None:
        if not data or len(data) > 65536:
            raise BoxieError(422, "invalid terminal input")
        async with self._lock(ref):
            record = self._record(ref)
            self._attachment(record, epoch)
            async with asyncio.timeout(self.config.docker_timeout):
                await self.terminals[ref.id].stream.write(data)
            if source == "user" and self._live(record):
                self.deadlines[ref.id].idle = self.monotonic() + record.spec.policy.idle_seconds
                record.last_input_at = self.clock()
                record.event_version += 1
                if self._input_flush_at is None:
                    self._input_flush_at = self.monotonic() + 1

    async def resize(self, ref: SandboxRef, epoch: int, columns: int, rows: int) -> None:
        if not 1 <= columns <= 500 or not 1 <= rows <= 300:
            raise BoxieError(422, "invalid terminal size")
        async with self._lock(ref):
            self._attachment(self._record(ref), epoch)
            async with asyncio.timeout(self.config.docker_timeout):
                await self.terminals[ref.id].resize(columns, rows)

    async def _reconcile_one(self, record: ExecutionRecord) -> None:
        async with self._lock(record.ref):
            if record.state in FINAL:
                return
            reason = record.reason if record.cleanup_pending else self._expiry(record)
            if reason:
                self._stop(record, reason)
        if reason:
            await self._cleanup(record)

    async def _supervised_reconcile(self, record: ExecutionRecord) -> None:
        try:
            await self._reconcile_one(record)
        except Exception:
            if record.id not in self._failed_reconciliations:
                logger.error("sandbox reconciliation failed; retry pending: %s", record.id)
            self._failed_reconciliations.add(record.id)
        else:
            self._failed_reconciliations.discard(record.id)

    async def reconcile(self) -> None:
        await asyncio.gather(
            *(self._supervised_reconcile(record) for record in list(self.records.values()))
        )

    async def start(self) -> None:
        # Missing process-local deadlines identify previous-boot sessions. Their
        # stop/save is supervised too, so a startup write fault remains retryable.
        await self.reconcile()
        self.sweeper = asyncio.create_task(self._sweep())

    async def compact(self) -> None:
        await compact(self)

    async def _sweep(self) -> None:
        while True:
            if not self._journal_healthy or (
                self._input_flush_at is not None and self.monotonic() >= self._input_flush_at
            ):
                try:
                    # Flush all in-memory pending decisions, including a failed
                    # final removal save, before advertising recovered durability.
                    self._save()
                except Exception:
                    pass
            if self.compactor is None or self.compactor.done():
                self.compactor = asyncio.create_task(self.compact())
            for record in list(self.records.values()):
                if record.state in FINAL:
                    continue
                previous = self.reconciliations.get(record.id)
                if previous is None or previous.done():
                    # The supervisor consumes and records failures. A cancelled
                    # per-record task is also replaced without ending this loop.
                    self.reconciliations[record.id] = asyncio.create_task(
                        self._supervised_reconcile(record)
                    )
            delay = min(1, self.config.sweep_seconds)
            if self._input_flush_at is not None:
                remaining = self._input_flush_at - self.monotonic()
                if remaining > 0:
                    delay = min(delay, remaining)
            await asyncio.sleep(delay)

    async def close(self) -> None:
        self.stopping = True
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        if self.compactor:
            self.compactor.cancel()
            await asyncio.gather(self.compactor, return_exceptions=True)
        await asyncio.gather(
            *(
                self.remove(r.ref, TerminationReason.ORCHESTRATOR_SHUTDOWN)
                for r in list(self.records.values())
                if r.state not in FINAL
            )
        )
        for worker in self.workers.values():
            worker.cancel()
        # Cancelling transport does not prove a daemon operation aborted: journal remains pending.
        for create in self.creates.values():
            create.cancel()
        await asyncio.gather(*self.workers.values(), *self.creates.values(), return_exceptions=True)
        await asyncio.gather(*self.reconciliations.values(), return_exceptions=True)
        await self.docker.close()
