"""Forget only durably removed, SQL-settled executions without live callbacks."""

import asyncio
from typing import TYPE_CHECKING

from boxie.domain.models import SandboxState
from boxie.sandbox.execution import ExecutionRecord

if TYPE_CHECKING:
    from boxie.sandbox.lifecycle import DurableAgent

MAX_RECORDS = 128


def settled(agent: DurableAgent, record: ExecutionRecord) -> bool:
    return (
        record.state in (SandboxState.TERMINATED, SandboxState.FAILED)
        and record.removal_confirmed
        and not record.cleanup_pending
        and not record.creation_uncertain
        and not record.start_uncertain
        and record.container_id is None
        and agent.durable_versions.get(record.id) == record.event_version
        and not agent.sessions.get(record.id)
        and all(
            record.id not in tasks or tasks[record.id].done()
            for tasks in (
                agent.creates,
                agent.workers,
                agent.reconciliations,
                agent.terminal_readers,
            )
        )
        and not agent.locks.get(record.id, asyncio.Lock()).locked()
        and not agent.cleanup_locks.get(record.id, asyncio.Lock()).locked()
    )


async def compact(agent: DurableAgent) -> None:
    for record in list(agent.records.values()):
        if not settled(agent, record):
            continue
        version = record.event_version
        try:
            # Never acquire SQL beneath a record lock (final-start uses SQL -> record).
            if not await agent.guard.terminal_receipt(record.request()):
                continue
        except Exception:
            continue  # An outage is not a missing admission or an eviction certificate.
        async with agent.admission_lock:
            if (
                agent.records.get(record.id) is not record
                or record.event_version != version
                or not settled(agent, record)
            ):
                continue
            # No await through replacement or map reclamation. Old lock waiters can
            # only find 404, and ensure must revalidate SQL before inserting a record.
            agent.records.pop(record.id)
            try:
                agent._save()
            except Exception:
                agent.records[record.id] = record
                continue
            for mapping in (
                agent.deadlines,
                agent.locks,
                agent.cleanup_locks,
                agent.workers,
                agent.creates,
                agent.terminals,
                agent.terminal_readers,
                agent.reconciliations,
                agent._reported,
                agent.durable_versions,
                agent.sessions,
            ):
                mapping.pop(record.id, None)
            agent._failed_reconciliations.discard(record.id)
            if agent.forget_observation:
                agent.forget_observation(record.id)
