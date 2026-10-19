"""Fenced execution of SQL admissions; PostgreSQL owns all reservations."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4

from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import (
    EnsureRequest,
    OrchestratorObservation,
    SandboxRef,
    StartGuard,
)
from boxie.domain.models import (
    LifetimePolicy,
    OrchestratorIdentity,
    Principal,
    ProfileSnapshot,
    SandboxSpec,
    SandboxState,
    TerminationReason,
)
from boxie.domain.policy import validate_policy
from boxie.observability.events import DiagnosticSink, Transition, deliver, emit
from boxie.sandbox.docker import ExecutionDocker
from boxie.sandbox.execution.journal import ExecutionJournal, ExecutionRecord
from boxie.sandbox.execution.reconcile import reconcile_removal
from boxie.sandbox.execution.retention import MAX_RECORDS, compact
from boxie.sandbox.execution.safety import ExecutionSafety
from boxie.sandbox.execution.sandbox import Deadlines, SandboxExecution, TerminalSession
from boxie.sandbox.profiles import ProfileRegistry

logger = logging.getLogger(__name__)

FINAL = {SandboxState.TERMINATED, SandboxState.FAILED}
ArtifactPath = Callable[[Principal, UUID], Awaitable[Path]]


class ExecutionConfig(Protocol):
    """Execution settings supplied by composition; transport credentials stay outside."""

    @property
    def data_dir(self) -> Path: ...
    @property
    def profiles_file(self) -> Path: ...
    @property
    def identity(self) -> OrchestratorIdentity: ...
    @property
    def policy_maximum(self) -> LifetimePolicy: ...
    @property
    def docker_timeout(self) -> float: ...
    @property
    def sweep_seconds(self) -> float: ...


class Orchestrator:
    def __init__(
        self,
        config: ExecutionConfig,
        docker: ExecutionDocker,
        artifact_path: ArtifactPath,
        guard: StartGuard,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
        *,
        diagnostics: DiagnosticSink = emit,
    ) -> None:
        self.config, self.docker, self.artifact_path, self.guard = (
            config,
            docker,
            artifact_path,
            guard,
        )
        self.clock, self.monotonic, self.boot_id = clock, monotonic, uuid4()
        self.diagnostics = diagnostics
        self.profiles = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
        self.journal = ExecutionJournal(config.data_dir / "execution.json", config.identity)
        self.records = self.journal.records
        self.admission_lock = asyncio.Lock()
        self.executions = {
            key: SandboxExecution(self, record, persisted_version=record.event_version)
            for key, record in self.records.items()
        }
        self._input_flush_at: float | None = None
        self.sweeper: asyncio.Task[None] | None = None
        self.safety = ExecutionSafety(self)
        self.stopping = False
        self.observer: Callable[[OrchestratorObservation], Awaitable[None]] | None = None
        self._journal_healthy = True

    async def resolve_admission(self, spec: SandboxSpec) -> ProfileSnapshot:
        return self.profiles.resolve_profile(spec.profile_id, spec.command, spec.env)

    def _record(self, ref: SandboxRef) -> ExecutionRecord:
        record = self.records.get(ref.id)
        if record is None or record.generation != ref.generation:
            raise BoxieError(404, "sandbox generation not found")
        return record

    def _lock(self, ref: SandboxRef) -> asyncio.Lock:
        self._record(ref)
        return self.executions[ref.id].lock

    @property
    def reconciliation_failed(self) -> bool:
        return any(execution.reconciliation_failed for execution in self.executions.values())

    def terminal_ref(self, principal: Principal, identifier: UUID) -> SandboxRef:
        record = self.records.get(identifier)
        if record is None or record.consumer_id != principal.consumer_id:
            raise BoxieError(404, "execution not found")
        if record.state != SandboxState.READY or self.executions[identifier].terminal is None:
            raise BoxieError(409, "terminal unavailable")
        return record.ref

    def terminal_session(self, ref: SandboxRef) -> TerminalSession:
        self._record(ref)
        return TerminalSession(self.executions[ref.id])

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
            and not self.reconciliation_failed
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
        for key, record in self.records.items():
            self.executions[key].persisted_version = record.event_version

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

    async def publish(self, ref: SandboxRef) -> None:
        if self.observer is not None:
            try:
                async with asyncio.timeout(5):
                    await self.observer(await self.inspect(ref))
            except Exception:
                pass  # Retried activity inspection repairs a lost live notification.

    async def inspect(self, ref: SandboxRef) -> OrchestratorObservation:
        async with self._lock(ref):
            return self._observation(self._record(ref))

    async def initialize(self, request: EnsureRequest) -> OrchestratorObservation:
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
                raise BoxieError(409, "committed admission unavailable")
            # Existing cleanup keeps progressing; only new identities consume headroom.
            limit = MAX_RECORDS if mode == "execute" else MAX_RECORDS + 8
            if (
                self.stopping
                or not self._journal_healthy
                or (mode == "execute" and self.reconciliation_failed)
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
            self.executions[record.id] = SandboxExecution(
                self,
                record,
                deadlines=Deadlines(
                    self.monotonic() + max(0, (request.absolute_deadline - now).total_seconds()),
                    self.monotonic() + 90,
                ),
            )
            try:
                self._save()
            except Exception:
                # The atomic replacement may have succeeded before fsync failed.
                # Keep the identity fenced and retry cleanup, never start this request.
                record.state = SandboxState.TERMINATING
                record.cleanup_pending = True
                record.reason = TerminationReason.PROVISIONING_FAILED
                self.executions[record.id].safety_required = True
                record.event_version += 1
                raise BoxieError(503, "orchestrator execution unavailable") from None
            return self._observation(record)

    async def ensure_ready(self, request: EnsureRequest) -> OrchestratorObservation:
        await self.initialize(request)
        execution = self.executions[request.ref.id]
        async with execution.provision_lock:
            record = execution.record
            if record.state == SandboxState.READY or record.state in FINAL:
                return await self.inspect(request.ref)
            pending = execution.create
            if pending is not None and not pending.done():
                # A timed-out/cancelled activity does not cancel a Docker create.
                await asyncio.shield(pending)
            execution.activity = asyncio.current_task()
            try:
                if record.cleanup_pending:
                    await self._cleanup(record)
                else:
                    await execution.provision()
            finally:
                execution.activity = None
            return await self.inspect(request.ref)

    def _expiry(self, record: ExecutionRecord) -> TerminationReason | None:
        timers = self.executions[record.id].deadlines
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
            and not self.reconciliation_failed
            and record.boot_id == self.boot_id
            and record.state not in FINAL | {SandboxState.TERMINATING}
            and self._expiry(record) is None
        )

    async def _ready(self, record: ExecutionRecord) -> bool:
        async with self._lock(record.ref):
            if not self._live(record):
                return False
            now = self.clock()
            record.state, record.ready_at, record.disconnected_at = SandboxState.READY, now, now
            timers = self.executions[record.id].deadlines
            assert timers is not None
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
            # is already saved in the journal; retain the in-memory stop latch and
            # attempt only ownership-checked removal. Confirmation requires a final save.
            self.executions[record.id].safety_required = True

    async def remove(self, ref: SandboxRef, reason: TerminationReason) -> OrchestratorObservation:
        async with self._lock(ref):
            record = self._record(ref)
            self._stop(record, reason)
        await self._cleanup(record)
        await self.publish(ref)
        return await self.inspect(ref)

    async def _cleanup(self, record: ExecutionRecord) -> None:
        async with self.executions[record.id].cleanup_lock:
            if record.state in FINAL:
                return
            execution = self.executions[record.id]
            terminal, execution.terminal = execution.terminal, None
            if terminal:
                if terminal.reader:
                    execution.terminal_reader = terminal.reader
                try:
                    async with asyncio.timeout(self.config.docker_timeout):
                        await terminal.close()
                except Exception:
                    pass
            create = execution.create
            started = self.monotonic()
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
            finally:
                if not record.removal_confirmed:
                    deliver(
                        self.diagnostics,
                        Transition(
                            event="cleanup.failed",
                            sandbox_id=record.id,
                            outcome="pending",
                            reason="delete_unconfirmed",
                            duration_seconds=max(0, self.monotonic() - started),
                        ),
                    )

    async def renew_control_lease(self, ref: SandboxRef, boot_id: UUID) -> OrchestratorObservation:
        async with self._lock(ref):
            record = self._record(ref)
            if boot_id != self.boot_id or not self._live(record):
                raise BoxieError(409, "control lease fence expired")
            self.executions[ref.id].timers.lease = self.monotonic() + 90
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
                self.executions[ref.id].pty.replay.after(resume_after)
            record.attachment_order = attachment_order
            record.attachment_epoch += 1
            record.attached, record.disconnected_at = True, None
            self.executions[ref.id].timers.disconnect = None
            self._changed(record)
            epoch = record.attachment_epoch
        deliver(self.diagnostics, Transition(event="terminal.attached", sandbox_id=record.id))
        await self.publish(ref)
        return epoch

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
        detached = False
        async with self._lock(ref):
            record = self._record(ref)
            if (
                record.state == SandboxState.READY
                and epoch == record.attachment_epoch
                and record.attached
            ):
                record.attached = False
                record.disconnected_at = self.clock()
                self.executions[ref.id].timers.disconnect = (
                    self.monotonic() + record.spec.policy.disconnect_seconds
                )
                self._changed(record)
                detached = True
        if detached:
            deliver(self.diagnostics, Transition(event="terminal.detached", sandbox_id=record.id))
        await self.publish(ref)

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
                await self.executions[ref.id].pty.stream.write(data)
            if source == "user" and self._live(record):
                self.executions[ref.id].timers.idle = (
                    self.monotonic() + record.spec.policy.idle_seconds
                )
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
                await self.executions[ref.id].pty.resize(columns, rows)

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
        attempted = record.state not in FINAL and (
            record.cleanup_pending or self._expiry(record) is not None
        )
        try:
            await self._reconcile_one(record)
        except Exception:
            if not self.executions[record.id].reconciliation_failed:
                logger.error("sandbox reconciliation failed; retry pending: %s", record.id)
            self.executions[record.id].reconciliation_failed = True
        else:
            self.executions[record.id].reconciliation_failed = False
        if attempted:
            deliver(
                self.diagnostics,
                Transition(
                    event="reconciliation.completed",
                    sandbox_id=record.id,
                    outcome="confirmed" if record.removal_confirmed else "pending",
                ),
            )

    async def reconcile(self) -> None:
        await asyncio.gather(
            *(self._supervised_reconcile(record) for record in list(self.records.values()))
        )

    async def start(self) -> None:
        # Missing process-local deadlines identify previous-boot sessions. Their
        # stop/save is supervised too, so a startup write fault remains retryable.
        await self.safety.inspect()
        self.safety.start()
        self.sweeper = self.safety.task

    async def compact(self) -> None:
        await compact(self)

    async def close(self) -> None:
        self.stopping = True
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        await asyncio.gather(
            *(
                self.remove(r.ref, TerminationReason.ORCHESTRATOR_SHUTDOWN)
                for r in list(self.records.values())
                if r.state not in FINAL
            )
        )
        for worker in (r.activity for r in self.executions.values() if r.activity is not None):
            worker.cancel()
        # Cancelling transport does not prove a daemon operation aborted: journal remains pending.
        for create in (r.create for r in self.executions.values() if r.create is not None):
            create.cancel()
        await asyncio.gather(
            *(
                task
                for execution in self.executions.values()
                for task in (execution.activity, execution.create, execution.reconciliation)
                if task is not None
            ),
            return_exceptions=True,
        )
        await self.docker.close()
