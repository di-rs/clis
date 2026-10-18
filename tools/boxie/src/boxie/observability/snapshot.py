"""One bounded SQLModel snapshot statement; a scrape never queries PostgreSQL."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.sql.expression import Select

from boxie.bootstrap.config import read_credential
from boxie.observability.metrics import KINDS, STATES, Metrics, OperationalSnapshot
from boxie.persistence.database import SessionFactory, database
from boxie.persistence.models import CapacitySlot, Outbox, Sandbox


async def operational_snapshot(sessions: SessionFactory) -> OperationalSnapshot:
    # All scalar subqueries run within the same PostgreSQL statement snapshot.
    state_counts = [func.count().filter(col(Sandbox.state) == state) for state in STATES]
    cleanup = col(Sandbox.cleanup_pending).is_(True)
    pending_counts = [
        select(func.count())
        .select_from(Outbox)
        .where(Outbox.kind == kind, col(Outbox.delivered_at).is_(None))
        .scalar_subquery()
        for kind in KINDS
    ]
    pending_oldest = [
        select(func.min(col(Outbox.created_at)))
        .where(Outbox.kind == kind, col(Outbox.delivered_at).is_(None))
        .scalar_subquery()
        for kind in KINDS
    ]
    statement: Select[*tuple[Any, ...]] = Select(
        func.statement_timestamp(),
        *state_counts,
        select(func.count(col(CapacitySlot.sandbox_id))).scalar_subquery(),
        select(func.count()).select_from(CapacitySlot).scalar_subquery(),
        func.count().filter(cleanup),
        func.count().filter(cleanup, col(Sandbox.cleanup_pending_since).is_(None)),
        func.min(col(Sandbox.cleanup_pending_since)).filter(cleanup),
        *pending_counts,
        *pending_oldest,
    )
    async with asyncio.timeout(2), sessions() as session, session.begin():
        await session.exec(select(func.set_config("statement_timeout", "1000", True)))
        row = (await session.exec(statement)).one()
    now = cast(datetime, row[0])
    offset = 1 + len(STATES)
    reserved, limit, pending, unknown, oldest = row[offset : offset + 5]
    counts = row[offset + 5 : offset + 5 + len(KINDS)]
    times = row[offset + 5 + len(KINDS) :]

    def age(value: datetime | None) -> float:
        return max(0, (now - value).total_seconds()) if value is not None else 0

    return OperationalSnapshot(
        timestamp=now.timestamp(),
        states=tuple(int(n) for n in row[1:offset]),
        reserved=int(reserved),
        limit=int(limit),
        cleanup_pending=int(pending),
        cleanup_age=age(oldest),
        cleanup_unknown=int(unknown),
        outbox=tuple(
            (int(count), age(created)) for count, created in zip(counts, times, strict=True)
        ),
    )


async def refresh_snapshots(metrics: Metrics, dsn_file: Path) -> None:
    # A separate single-connection SQLAlchemy engine cannot consume admission slots.
    async with database(
        read_credential(dsn_file, allow_spaces=True).decode(),
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
    ) as sessions:
        while True:
            try:
                metrics.refresh(await operational_snapshot(sessions))
                metrics.dependency("postgres", True)
            except Exception:
                metrics.refresh_failed()
                metrics.dependency("postgres", False)
            await asyncio.sleep(5)
