"""Forget only durably removed, SQL-settled executions without live callbacks."""

import asyncio
from typing import TYPE_CHECKING

from boxie.domain.models import SandboxState
from boxie.sandbox.execution import ExecutionRecord

if TYPE_CHECKING:
    from boxie.sandbox.lifecycle import DurableOrchestrator

MAX_RECORDS = 128


def settled(orchestrator: DurableOrchestrator, record: ExecutionRecord) -> bool:
    return (
        record.state in (SandboxState.TERMINATED, SandboxState.FAILED)
        and record.removal_confirmed
        and not record.cleanup_pending
        and not record.creation_uncertain
        and not record.start_uncertain
        and record.container_id is None
        and orchestrator.durable_versions.get(record.id) == record.event_version
        and not orchestrator.sessions.get(record.id)
        and all(
            record.id not in tasks or tasks[record.id].done()
            for tasks in (
                orchestrator.creates,
                orchestrator.workers,
                orchestrator.reconciliations,
                orchestrator.terminal_readers,
            )
        )
        and not orchestrator.locks.get(record.id, asyncio.Lock()).locked()
        and not orchestrator.cleanup_locks.get(record.id, asyncio.Lock()).locked()
    )


async def compact(orchestrator: DurableOrchestrator) -> None:
    for record in list(orchestrator.records.values()):
        if not settled(orchestrator, record):
            continue
        version = record.event_version
        try:
            # Never acquire SQL beneath a record lock (final-start uses SQL -> record).
            if not await orchestrator.guard.terminal_receipt(record.request()):
                continue
        except Exception:
            continue  # An outage is not a missing admission or an eviction certificate.
        async with orchestrator.admission_lock:
            if (
                orchestrator.records.get(record.id) is not record
                or record.event_version != version
                or not settled(orchestrator, record)
            ):
                continue
            # No await through replacement or map reclamation. Old lock waiters can
            # only find 404, and ensure must revalidate SQL before inserting a record.
            orchestrator.records.pop(record.id)
            try:
                orchestrator._save()
            except Exception:
                orchestrator.records[record.id] = record
                continue
            for mapping in (
                orchestrator.deadlines,
                orchestrator.locks,
                orchestrator.cleanup_locks,
                orchestrator.workers,
                orchestrator.creates,
                orchestrator.terminals,
                orchestrator.terminal_readers,
                orchestrator.reconciliations,
                orchestrator._reported,
                orchestrator.durable_versions,
                orchestrator.sessions,
            ):
                mapping.pop(record.id, None)
            orchestrator._failed_reconciliations.discard(record.id)
            if orchestrator.forget_observation:
                orchestrator.forget_observation(record.id)
