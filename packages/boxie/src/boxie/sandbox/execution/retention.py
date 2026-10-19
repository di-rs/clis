"""Forget only journal-confirmed removals settled in SQL without live callbacks."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from boxie.sandbox.execution.supervisor import Supervisor

MAX_RECORDS = 128


async def compact(supervisor: Supervisor) -> None:
    for record in list(supervisor.records.values()):
        execution = supervisor.executions.get(record.id)
        if (
            execution is None
            or supervisor.records.get(record.id) is not record
            or not execution.settled
        ):
            continue
        version = record.event_version
        try:
            # Never acquire SQL beneath a record lock (final-start uses SQL -> record).
            if not await supervisor.guard.terminal_receipt(record.request()):
                continue
        except Exception:
            continue  # An outage is not a missing admission or an eviction certificate.
        async with supervisor.admission_lock:
            if (
                supervisor.records.get(record.id) is not record
                or record.event_version != version
                or not supervisor.executions[record.id].settled
            ):
                continue
            # No await through replacement or map reclamation. Old lock waiters can
            # only find 404, and ensure must revalidate SQL before inserting a record.
            supervisor.records.pop(record.id)
            try:
                supervisor._save()
            except Exception:
                supervisor.records[record.id] = record
                continue
            supervisor.executions.pop(record.id)
