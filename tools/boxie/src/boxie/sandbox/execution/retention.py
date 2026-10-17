"""Forget only journal-confirmed removals settled in SQL without live callbacks."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from boxie.sandbox.execution.orchestrator import Orchestrator

MAX_RECORDS = 128


async def compact(orchestrator: Orchestrator) -> None:
    for record in list(orchestrator.records.values()):
        if not orchestrator.runtimes[record.id].settled:
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
                or not orchestrator.runtimes[record.id].settled
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
            orchestrator.runtimes.pop(record.id)
