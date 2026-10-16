"""Short SKIP LOCKED transactions with owner-fenced acknowledgements."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from boxie.persistence.database import SessionFactory
from boxie.persistence.models import CapacitySlot, Outbox, Sandbox, SandboxArtifactRef


@dataclass(frozen=True)
class OutboxEvent:
    id: UUID
    sandbox_id: UUID
    lease_owner: str
    lease_expires_at: datetime
    attempts: int


class PostgresOutbox:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    async def claim(self, owner: str) -> list[OutboxEvent]:
        async with self.sessions() as session, session.begin():
            rows = (
                await session.exec(
                    select(Outbox)
                    .where(
                        col(Outbox.delivered_at).is_(None),
                        Outbox.next_attempt_at <= func.clock_timestamp(),
                        or_(
                            col(Outbox.lease_expires_at).is_(None),
                            col(Outbox.lease_expires_at) <= func.clock_timestamp(),
                        ),
                    )
                    .order_by(col(Outbox.created_at), col(Outbox.id))
                    .with_for_update(skip_locked=True)
                    .limit(32)
                )
            ).all()
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            events = []
            for row in rows:
                row.lease_owner, row.lease_expires_at = owner, now + timedelta(seconds=30)
                row.attempts += 1
                events.append(
                    OutboxEvent(row.id, row.sandbox_id, owner, row.lease_expires_at, row.attempts)
                )
            return events

    @staticmethod
    async def _owned(session: AsyncSession, event: OutboxEvent) -> Outbox | None:
        return (
            await session.exec(
                select(Outbox)
                .where(
                    Outbox.id == event.id,
                    Outbox.lease_owner == event.lease_owner,
                    col(Outbox.lease_expires_at) > func.clock_timestamp(),
                    col(Outbox.delivered_at).is_(None),
                )
                .with_for_update()
            )
        ).one_or_none()

    async def valid(self, event: OutboxEvent) -> bool:
        async with self.sessions() as session:
            return (
                await session.exec(
                    select(Outbox.id).where(
                        Outbox.id == event.id,
                        Outbox.lease_owner == event.lease_owner,
                        col(Outbox.lease_expires_at)
                        > func.clock_timestamp() + timedelta(seconds=5),
                        col(Outbox.delivered_at).is_(None),
                    )
                )
            ).first() is not None

    async def acknowledge(self, event: OutboxEvent) -> bool:
        async with self.sessions() as session, session.begin():
            row = await self._owned(session, event)
            if row is None:
                return False
            row.delivered_at = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            row.lease_owner, row.lease_expires_at = None, None
            return True

    async def retry(self, event: OutboxEvent) -> None:
        async with self.sessions() as session, session.begin():
            row = await self._owned(session, event)
            if row is not None:
                now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
                row.lease_owner, row.lease_expires_at = None, None
                row.next_attempt_at = now + timedelta(
                    seconds=min(30, 2 ** min(5, event.attempts - 1))
                )

    async def confirmed_terminal(self, sandbox_id: UUID) -> bool:
        async with self.sessions() as session:
            return (
                await session.exec(
                    select(Sandbox.id).where(
                        Sandbox.id == sandbox_id,
                        col(Sandbox.cleaned_at).is_not(None),
                        col(Sandbox.cleanup_pending).is_(False),
                        ~select(CapacitySlot).where(CapacitySlot.sandbox_id == Sandbox.id).exists(),
                        ~select(SandboxArtifactRef)
                        .where(SandboxArtifactRef.sandbox_id == Sandbox.id)
                        .exists(),
                    )
                )
            ).first() is not None
