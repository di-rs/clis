"""Transactional command handoff; duplicate deliveries are safe."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlmodel import col, select

from boxie.domain.models import SupervisorIdentity
from boxie.persistence.database import SessionFactory
from boxie.persistence.models import CapacitySlot, Outbox, Sandbox, SandboxArtifactRef


class OutboxEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: UUID
    sandbox_id: UUID
    request_id: UUID | None = None


class PostgresOutbox:
    def __init__(
        self, sessions: SessionFactory, identity: SupervisorIdentity | None = None
    ) -> None:
        self.sessions, self.identity = sessions, identity

    async def pending(self, sandbox_id: UUID | None = None) -> list[OutboxEvent]:
        async with self.sessions() as session:
            query = select(Outbox).where(col(Outbox.delivered_at).is_(None))
            if self.identity is not None:
                query = query.join(Sandbox, col(Outbox.sandbox_id) == col(Sandbox.id)).where(
                    Sandbox.installation_id == self.identity.installation_id,
                    Sandbox.host_id == self.identity.host_id,
                )
            if sandbox_id is not None:
                query = query.where(Outbox.sandbox_id == sandbox_id)
            rows = (
                await session.exec(query.order_by(col(Outbox.created_at), col(Outbox.id)).limit(32))
            ).all()
            return [
                OutboxEvent(id=row.id, sandbox_id=row.sandbox_id, request_id=row.request_id)
                for row in rows
            ]

    async def acknowledge(self, event: OutboxEvent) -> bool:
        async with self.sessions() as session, session.begin():
            row = (
                await session.exec(select(Outbox).where(Outbox.id == event.id).with_for_update())
            ).one_or_none()
            if row is None:
                return False
            if row.delivered_at is None:
                row.delivered_at = await session.scalar(select(func.clock_timestamp()))
            return True

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
