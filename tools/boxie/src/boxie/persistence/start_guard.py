"""Orchestrator-owned final-start guard sharing durable stop's sandbox row lock."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from boxie.contracts.consumer import spec_wire
from boxie.contracts.execution import SnapshotWire
from boxie.domain.lifecycle import EnsureRequest
from boxie.domain.models import TerminationReason
from boxie.persistence.database import SessionFactory
from boxie.persistence.models import Artifact, CapacitySlot, Sandbox, SandboxArtifactRef


def matches(row: Sandbox, request: EnsureRequest) -> bool:
    return (
        row.consumer_id == request.principal.consumer_id
        and row.installation_id == request.identity.installation_id
        and row.host_id == request.identity.host_id
        and row.generation == request.ref.generation
        and row.spec == spec_wire(request.spec).model_dump(mode="json")
        and row.snapshot
        == SnapshotWire.from_domain(request.profile_snapshot).model_dump(mode="json")
        and row.accepted_at == request.accepted_at
        and row.absolute_deadline == request.absolute_deadline
    )


class PostgresStartGuard:
    def __init__(self, sessions: SessionFactory, timeout: float = 20) -> None:
        if not 0 < timeout <= 60:
            raise ValueError("bounded start timeout required")
        self.sessions, self.timeout = sessions, timeout

    async def _allowed(self, session: AsyncSession, request: EnsureRequest, boot_id: UUID) -> bool:
        row = (
            await session.exec(
                select(Sandbox)
                .where(
                    Sandbox.id == request.ref.id,
                    Sandbox.consumer_id == request.principal.consumer_id,
                    Sandbox.installation_id == request.identity.installation_id,
                    Sandbox.host_id == request.identity.host_id,
                    Sandbox.generation == request.ref.generation,
                    Sandbox.orchestrator_boot_id == boot_id,
                    col(Sandbox.desired_stop).is_(False),
                    col(Sandbox.cleaned_at).is_(None),
                    col(Sandbox.state).in_(["accepted", "provisioning", "ready"]),
                    Sandbox.absolute_deadline > func.clock_timestamp(),
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None or not matches(row, request):
            return False
        return (
            await session.exec(
                select(SandboxArtifactRef.sandbox_id)
                .join(Artifact, col(Artifact.id) == col(SandboxArtifactRef.artifact_id))
                .where(
                    SandboxArtifactRef.sandbox_id == request.ref.id,
                    SandboxArtifactRef.artifact_id == request.spec.artifact_id,
                    Artifact.consumer_id == request.principal.consumer_id,
                )
            )
        ).first() is not None

    async def initialization(
        self, request: EnsureRequest, boot_id: UUID
    ) -> Literal["execute"] | TerminationReason | None:
        async with asyncio.timeout(self.timeout), self.sessions() as session:
            result = (
                await session.exec(
                    select(
                        Sandbox,
                        (Sandbox.absolute_deadline > func.clock_timestamp()),
                        select(CapacitySlot).where(CapacitySlot.sandbox_id == Sandbox.id).exists(),
                        select(SandboxArtifactRef)
                        .where(
                            SandboxArtifactRef.sandbox_id == Sandbox.id,
                            SandboxArtifactRef.artifact_id == request.spec.artifact_id,
                        )
                        .exists(),
                    ).where(
                        Sandbox.id == request.ref.id,
                        Sandbox.orchestrator_boot_id == boot_id,
                        col(Sandbox.cleaned_at).is_(None),
                    )
                )
            ).one_or_none()
        if result is None:
            return None
        row, fresh, reserved, retained = result
        if not matches(row, request) or not reserved or not retained:
            return None
        if fresh and not row.desired_stop and row.state in ("accepted", "provisioning", "ready"):
            return "execute"
        return (
            TerminationReason(row.reason)
            if row.reason
            else (TerminationReason.REQUESTED if row.desired_stop else TerminationReason.EXPIRED)
        )

    async def terminal_receipt(self, request: EnsureRequest) -> bool:
        """SQL finality survives boot changes and retry-row purging."""
        async with asyncio.timeout(self.timeout), self.sessions() as session:
            result = (
                await session.exec(
                    select(
                        Sandbox,
                        select(CapacitySlot).where(CapacitySlot.sandbox_id == Sandbox.id).exists(),
                        select(SandboxArtifactRef)
                        .where(SandboxArtifactRef.sandbox_id == Sandbox.id)
                        .exists(),
                    ).where(Sandbox.id == request.ref.id)
                )
            ).one_or_none()
        if result is None:
            return True
        row, reserved, retained = result
        return bool(
            matches(row, request)
            and row.cleaned_at is not None
            and row.state in ("terminated", "failed")
            and not row.cleanup_pending
            and not reserved
            and not retained
        )

    async def start_allowed(self, request: EnsureRequest, boot_id: UUID) -> bool:
        async with asyncio.timeout(self.timeout), self.sessions() as session, session.begin():
            return await self._allowed(session, request, boot_id)

    async def run_start_if_allowed(
        self, request: EnsureRequest, boot_id: UUID, start: Callable[[], Awaitable[None]]
    ) -> bool:
        # Only the bounded final start runs under the lock; create/attach never do.
        async with asyncio.timeout(self.timeout), self.sessions() as session, session.begin():
            if not await self._allowed(session, request, boot_id):
                return False
            await start()
            return True
