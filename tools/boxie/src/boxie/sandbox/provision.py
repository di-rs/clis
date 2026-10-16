"""Create/attach/final-start sequencing, including uncertain daemon outcomes."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from boxie.domain.lifecycle import OrchestratorObservation, SandboxRef, StartGuard
from boxie.domain.models import Principal, SandboxState, TerminationReason
from boxie.sandbox.docker import CreateRejected, ExecutionDocker, container_config
from boxie.sandbox.execution import ExecutionRecord
from boxie.sandbox.reconcile import owned_container
from boxie.sandbox.terminal import ExecutionTerminal


@dataclass(frozen=True)
class ProvisionDecisions:
    """Explicit lifecycle callbacks; no access to admission or general orchestrator internals.

    Lock order: SQL row -> local decision lock for bounded start. Never acquire
    SQL from inside a local decision lock. Create/attach hold neither lock.
    """

    lock: Callable[[SandboxRef], asyncio.Lock]
    live: Callable[[ExecutionRecord], bool]
    expiry: Callable[[ExecutionRecord], TerminationReason | None]
    changed: Callable[[ExecutionRecord], None]
    remove: Callable[[SandboxRef, TerminationReason], Awaitable[OrchestratorObservation]]
    cleanup: Callable[[ExecutionRecord], Awaitable[None]]
    ready: Callable[[ExecutionRecord], Awaitable[bool]]
    creating: Callable[[UUID, asyncio.Task[None]], None]
    attached: Callable[[UUID, ExecutionTerminal], None]


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


async def provision(
    record: ExecutionRecord,
    docker: ExecutionDocker,
    artifact_path: Callable[[Principal, UUID], Awaitable[Path]],
    guard: StartGuard,
    boot_id: UUID,
    timeout: float,
    decisions: ProvisionDecisions,
) -> None:
    try:
        if not decisions.live(record):
            await decisions.remove(
                record.ref, decisions.expiry(record) or TerminationReason.REQUESTED
            )
            return
        request = record.request()
        async with asyncio.timeout(timeout):
            if not await guard.start_allowed(request, boot_id):
                await decisions.remove(record.ref, TerminationReason.REQUESTED)
                return
            # Guard verifies the retained durable artifact reference before mount lookup.
            artifact = await artifact_path(request.principal, request.spec.artifact_id)
            info = await owned_container(docker, record)
        async with decisions.lock(record.ref):
            live = decisions.live(record)
            if live:
                record.state = SandboxState.PROVISIONING
                decisions.changed(record)
        if not live:
            await decisions.remove(
                record.ref, decisions.expiry(record) or TerminationReason.REQUESTED
            )
            return
        if info is None:
            if not decisions.live(record):
                return
            record.creation_uncertain = True
            record.operation_id = uuid4()
            decisions.changed(record)
            create = asyncio.create_task(_create(record, artifact, docker, decisions.changed))
            decisions.creating(record.id, create)
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.shield(create)
            except TimeoutError:
                await decisions.remove(record.ref, TerminationReason.PROVISIONING_FAILED)
                # Retain the handle: caller timeout does not cancel daemon execution.
                await create
                await decisions.cleanup(record)
                return
            async with asyncio.timeout(timeout):
                info = await owned_container(docker, record)
        if info is None:
            raise RuntimeError("create outcome unknown")
        if record.state in {SandboxState.TERMINATED, SandboxState.FAILED}:
            return
        record.container_id = info.id
        record.creation_uncertain = False
        decisions.changed(record)
        if not decisions.live(record):
            await decisions.remove(
                record.ref, decisions.expiry(record) or TerminationReason.REQUESTED
            )
            return
        async with asyncio.timeout(timeout):
            stream = await docker.attach(info.id)
        terminal = ExecutionTerminal(
            stream,
            record.snapshot.limits.output_bytes,
            lambda reason: decisions.remove(record.ref, reason),
        )
        if not decisions.live(record):
            async with asyncio.timeout(timeout):
                await terminal.close()
            await decisions.remove(
                record.ref, decisions.expiry(record) or TerminationReason.REQUESTED
            )
            return
        decisions.attached(record.id, terminal)
        terminal.start()

        async def start() -> None:
            async with decisions.lock(record.ref):
                if not decisions.live(record):
                    raise RuntimeError("local start denied")
                record.start_uncertain = True
                record.operation_id = uuid4()
                decisions.changed(record)
                async with asyncio.timeout(timeout):
                    await docker.start(info.id)
                record.start_uncertain = False
                decisions.changed(record)

        async with asyncio.timeout(timeout + 1):
            allowed = await guard.run_start_if_allowed(request, boot_id, start)
            allowed = allowed and await guard.start_allowed(request, boot_id)
        if allowed:
            allowed = await decisions.ready(record)
        if not allowed:
            await decisions.remove(
                record.ref, decisions.expiry(record) or TerminationReason.REQUESTED
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        await decisions.remove(record.ref, TerminationReason.PROVISIONING_FAILED)
