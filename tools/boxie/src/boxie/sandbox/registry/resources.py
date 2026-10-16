"""Docker provisioning and confirmed cleanup of admitted sandbox resources."""

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from boxie.domain.models import (
    SandboxState,
    SandboxView,
    TerminationReason,
)
from boxie.sandbox.docker import container_config
from boxie.sandbox.registry.records import FINAL, Record
from boxie.sandbox.terminal import Terminal

if TYPE_CHECKING:
    from boxie.sandbox.registry.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


async def provision(owner: Orchestrator, record: Record) -> None:
    try:
        if owner.monotonic() >= owner._deadlines[record.id].absolute:
            await owner._cleanup(record, TerminationReason.EXPIRED)
            return
        record.state = SandboxState.PROVISIONING
        record.creation_uncertain = True
        owner._save()
        async with asyncio.timeout(owner.config.docker_timeout):
            await owner.docker.create(
                record.name,
                container_config(
                    record.snapshot.domain(),
                    owner.artifacts.path(record.principal, record.spec.artifact_id),
                    record.labels,
                ),
            )
            stream = await owner.docker.attach(record.name)
            terminal = Terminal(owner, record, stream)
            owner.terminals[record.id] = terminal
            await owner.docker.start(record.name)
        record.creation_uncertain = False
        record.state = SandboxState.READY
        record.idle_deadline = owner.clock() + timedelta(seconds=record.spec.policy.idle_seconds)
        deadlines = owner._deadlines[record.id]
        now = owner.monotonic()
        deadlines.idle = now + record.spec.policy.idle_seconds
        deadlines.disconnect = now + record.spec.policy.disconnect_seconds
        owner._save()
        terminal.start()
    except asyncio.CancelledError:
        # The remover joins this task before confirming daemon deletion.
        raise
    except Exception:
        logger.warning("sandbox provisioning failed: %s", record.id)
        await owner._cleanup(record, TerminationReason.PROVISIONING_FAILED)


async def cleanup(
    owner: Orchestrator, record: Record, reason: TerminationReason, *, conditional: bool = False
) -> SandboxView:
    lock = owner._operations.setdefault(record.id, asyncio.Lock())
    async with lock:
        if record.state in FINAL:
            return record.view()
        if conditional:
            if record.state != SandboxState.READY:
                return record.view()
            current_reason = owner._expiry_reason(record, owner.monotonic())
            if current_reason is None:
                return record.view()
            reason = current_reason
        # No await separates revalidation and the state transition. Input and
        # attachment mutations below share this event-loop serialization point.
        record.state = SandboxState.TERMINATING
        record.cleanup_pending = True
        record.reason = record.reason or reason
        owner._save()
        terminal = owner.terminals.pop(record.id, None)
        if terminal is not None:
            try:
                await terminal.close()
            except Exception:
                logger.warning("terminal transport close failed: %s", record.id)
        try:
            async with asyncio.timeout(owner.config.docker_timeout):
                labels = await owner.docker.labels(record.name)
                if labels is not None and any(
                    labels.get(key) != value for key, value in record.labels.items()
                ):
                    raise RuntimeError("container ownership mismatch")
                if labels is None and record.creation_uncertain:
                    # A timed-out create may still be running inside Docker. Absence
                    # is not confirmation of deletion while its result is unknown.
                    return record.view()
                if labels is not None:
                    await owner.docker.delete(record.name)
            record.creation_uncertain = False
            record.cleanup_pending = False
            record.state = (
                SandboxState.FAILED
                if record.reason == TerminationReason.PROVISIONING_FAILED
                else SandboxState.TERMINATED
            )
            owner._save()
            owner.artifacts.release(record.principal, record.spec.artifact_id, record.name)
            owner._deadlines.pop(record.id, None)
        except Exception:
            logger.warning("sandbox cleanup remains pending: %s", record.id)
        return record.view()
