"""One execution's process-local ownership, provisioning and terminal-session lifetime."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING
from uuid import uuid4

from boxie.domain.errors import BoxieError
from boxie.domain.models import SandboxState, TerminationReason
from boxie.sandbox.docker import CreateRejected, ExecutionDocker, container_config
from boxie.sandbox.execution.journal import ExecutionRecord
from boxie.sandbox.execution.reconcile import owned_container
from boxie.sandbox.terminal.pty import ExecutionTerminal

if TYPE_CHECKING:
    from boxie.sandbox.execution.orchestrator import Orchestrator


@dataclass
class Deadlines:
    absolute: float
    lease: float
    idle: float | None = None
    disconnect: float | None = None


@dataclass
class SandboxExecution:
    owner: Orchestrator
    record: ExecutionRecord
    deadlines: Deadlines | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    provision_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    activity: asyncio.Task[object] | None = None
    create: asyncio.Task[None] | None = None
    terminal: ExecutionTerminal | None = None
    terminal_reader: asyncio.Task[None] | None = None
    reconciliation: asyncio.Task[None] | None = None
    sessions: int = 0
    persisted_version: int = 0
    reconciliation_failed: bool = False
    safety_required: bool = False

    @property
    def timers(self) -> Deadlines:
        assert self.deadlines is not None
        return self.deadlines

    @property
    def pty(self) -> ExecutionTerminal:
        if self.terminal is None:
            raise BoxieError(409, "terminal unavailable")
        return self.terminal

    @property
    def settled(self) -> bool:
        record = self.record
        return (
            record.state in (SandboxState.TERMINATED, SandboxState.FAILED)
            and record.removal_confirmed
            and not record.cleanup_pending
            and not record.creation_uncertain
            and not record.start_uncertain
            and record.container_id is None
            and self.persisted_version == record.event_version
            and self.sessions == 0
            and all(
                task is None or task.done()
                for task in (self.create, self.activity, self.reconciliation, self.terminal_reader)
            )
            and not self.lock.locked()
            and not self.cleanup_lock.locked()
        )

    async def provision(self) -> None:
        owner, record = self.owner, self.record
        docker, artifact_path, guard = owner.docker, owner.artifact_path, owner.guard
        boot_id, timeout = owner.boot_id, owner.config.docker_timeout

        try:
            if not owner._live(record):
                await owner.remove(record.ref, owner._expiry(record) or TerminationReason.REQUESTED)
                return
            request = record.request()
            async with asyncio.timeout(timeout):
                if not await guard.start_allowed(request, boot_id):
                    await owner.remove(record.ref, TerminationReason.REQUESTED)
                    return
                # Guard verifies the retained SQL artifact reference before mount lookup.
                artifact = await artifact_path(request.principal, request.spec.artifact_id)
                info = await owned_container(docker, record)
            async with self.lock:
                live = owner._live(record)
                if live:
                    record.state = SandboxState.PROVISIONING
                    owner._changed(record)
            if not live:
                await owner.remove(record.ref, owner._expiry(record) or TerminationReason.REQUESTED)
                return
            if info is None:
                if not owner._live(record):
                    return
                record.creation_uncertain = True
                record.operation_id = uuid4()
                owner._changed(record)
                create = asyncio.create_task(_create(record, artifact, docker, owner._changed))
                self.create = create
                try:
                    async with asyncio.timeout(timeout):
                        await asyncio.shield(create)
                except TimeoutError:
                    await owner.remove(record.ref, TerminationReason.PROVISIONING_FAILED)
                    # Retain the handle: caller timeout does not cancel daemon execution.
                    await asyncio.shield(create)
                    await owner._cleanup(record)
                    return
                async with asyncio.timeout(timeout):
                    info = await owned_container(docker, record)
            if info is None:
                raise RuntimeError("create outcome unknown")
            if record.state in {SandboxState.TERMINATED, SandboxState.FAILED}:
                return
            record.container_id = info.id
            record.creation_uncertain = False
            owner._changed(record)
            if not owner._live(record):
                await owner.remove(record.ref, owner._expiry(record) or TerminationReason.REQUESTED)
                return
            terminal = self.terminal
            if terminal is None:
                async with asyncio.timeout(timeout):
                    stream = await docker.attach(info.id)
                terminal = ExecutionTerminal(
                    stream,
                    record.snapshot.limits.output_bytes,
                    lambda reason: owner.remove(record.ref, reason),
                )
                if not owner._live(record):
                    async with asyncio.timeout(timeout):
                        await terminal.close()
                    await owner.remove(
                        record.ref, owner._expiry(record) or TerminationReason.REQUESTED
                    )
                    return
                self.terminal = terminal
                terminal.start()
            # A cancelled start may have reached Docker. Reconcile physical state;
            # never issue a second start based on an uncertain acknowledgement.
            if record.start_uncertain and not info.running:
                raise RuntimeError("Docker start outcome uncertain")

            async def start() -> None:
                async with self.lock:
                    if not owner._live(record):
                        raise RuntimeError("local start denied")
                    record.start_uncertain = True
                    record.operation_id = uuid4()
                    owner._changed(record)
                    async with asyncio.timeout(timeout):
                        await docker.start(info.id)
                    record.start_uncertain = False
                    owner._changed(record)

            async with asyncio.timeout(timeout + 1):
                if info.running:
                    record.start_uncertain = False
                    owner._changed(record)
                    allowed = await guard.start_allowed(request, boot_id)
                else:
                    allowed = await guard.run_start_if_allowed(request, boot_id, start)
                allowed = allowed and await guard.start_allowed(request, boot_id)
            if allowed:
                allowed = await owner._ready(record)
            if not allowed:
                await owner.remove(record.ref, owner._expiry(record) or TerminationReason.REQUESTED)
        except asyncio.CancelledError:
            raise
        except Exception:
            await owner.remove(record.ref, TerminationReason.PROVISIONING_FAILED)


async def _create(
    record: ExecutionRecord,
    artifact: Path,
    docker: ExecutionDocker,
    changed: Callable[[ExecutionRecord], None],
) -> None:
    try:
        await docker.create(
            record.name, container_config(record.snapshot.domain(), artifact, record.labels)
        )
    except CreateRejected:
        record.creation_uncertain = False
        changed(record)
    except Exception:
        # Transport failure leaves the side effect unknown; owned discovery resolves it.
        pass
    else:
        record.creation_uncertain = False
        changed(record)


class TerminalSession(AbstractContextManager["TerminalSession"]):
    """Pins execution ownership until transport has drained and closed."""

    def __init__(self, execution: SandboxExecution) -> None:
        self.execution = execution
        self.terminal = execution.pty

    def __enter__(self) -> TerminalSession:
        self.execution.sessions += 1
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.execution.sessions -= 1

    @property
    def state(self) -> SandboxState:
        return self.execution.record.state

    @property
    def reason(self) -> TerminationReason | None:
        return self.execution.record.reason
