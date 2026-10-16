"""Agent-owned final-start guard sharing durable stop's sandbox row lock."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

import psycopg

from boxie.domain.lifecycle import EnsureRequest
from boxie.domain.models import TerminationReason
from boxie.persistence.postgres import Pool, Row
from boxie.sandbox.protocol import SnapshotWire, spec_wire


class PostgresStartGuard:
    def __init__(self, pool: Pool, timeout: float = 20) -> None:
        if not 0 < timeout <= 60:
            raise ValueError("bounded start timeout required")
        self.pool, self.timeout = pool, timeout

    async def _allowed(
        self, conn: psycopg.AsyncConnection[Row], request: EnsureRequest, boot_id: UUID
    ) -> bool:
        row = await (
            await conn.execute(
                """SELECT * FROM sandboxes WHERE id=%s AND consumer_id=%s
            AND installation_id=%s AND host_id=%s AND generation=%s AND agent_boot_id=%s
            AND NOT desired_stop AND cleaned_at IS NULL
            AND state IN ('accepted','provisioning','ready')
            AND absolute_deadline > clock_timestamp() FOR UPDATE""",
                (
                    request.ref.id,
                    request.principal.consumer_id,
                    request.identity.installation_id,
                    request.identity.host_id,
                    request.ref.generation,
                    boot_id,
                ),
            )
        ).fetchone()
        if row is None:
            return False
        if (
            row["spec"] != spec_wire(request.spec).model_dump(mode="json")
            or row["snapshot"]
            != SnapshotWire.from_domain(request.profile_snapshot).model_dump(mode="json")
            or row["accepted_at"] != request.accepted_at
            or row["absolute_deadline"] != request.absolute_deadline
        ):
            return False
        reference = await (
            await conn.execute(
                "SELECT r.sandbox_id FROM sandbox_artifact_refs r JOIN artifacts a ON a.id=r.artifact_id WHERE r.sandbox_id=%s AND r.artifact_id=%s AND a.consumer_id=%s",
                (request.ref.id, request.spec.artifact_id, request.principal.consumer_id),
            )
        ).fetchone()
        return reference is not None

    async def initialization(
        self, request: EnsureRequest, boot_id: UUID
    ) -> Literal["execute"] | TerminationReason | None:
        async with asyncio.timeout(self.timeout):
            async with self.pool.connection() as conn:
                row = await (
                    await conn.execute(
                        """SELECT s.*, absolute_deadline > clock_timestamp() AS fresh,
                    EXISTS (SELECT 1 FROM capacity_slots c WHERE c.sandbox_id=s.id) AS reserved,
                    EXISTS (SELECT 1 FROM sandbox_artifact_refs r WHERE r.sandbox_id=s.id AND r.artifact_id=%s) AS retained
                    FROM sandboxes s WHERE id=%s AND consumer_id=%s AND installation_id=%s AND host_id=%s AND generation=%s AND agent_boot_id=%s AND cleaned_at IS NULL""",
                        (
                            request.spec.artifact_id,
                            request.ref.id,
                            request.principal.consumer_id,
                            request.identity.installation_id,
                            request.identity.host_id,
                            request.ref.generation,
                            boot_id,
                        ),
                    )
                ).fetchone()
        if (
            row is None
            or not row["reserved"]
            or not row["retained"]
            or (
                row["spec"] != spec_wire(request.spec).model_dump(mode="json")
                or row["snapshot"]
                != SnapshotWire.from_domain(request.profile_snapshot).model_dump(mode="json")
                or row["accepted_at"] != request.accepted_at
                or row["absolute_deadline"] != request.absolute_deadline
            )
        ):
            return None
        if (
            not row["desired_stop"]
            and row["fresh"]
            and row["state"] in ("accepted", "provisioning", "ready")
        ):
            return "execute"
        return (
            TerminationReason(row["reason"])
            if row["reason"]
            else (TerminationReason.REQUESTED if row["desired_stop"] else TerminationReason.EXPIRED)
        )

    async def terminal_receipt(self, request: EnsureRequest) -> bool:
        """SQL finality survives boot changes and retry-row purging.

        The caller separately proves durable local absence and settled callbacks.
        A missing row is safe only because all new local records require initialization.
        """
        async with asyncio.timeout(self.timeout):
            async with self.pool.connection() as conn:
                row = await (
                    await conn.execute(
                        """SELECT s.*, NOT EXISTS (SELECT 1 FROM capacity_slots c WHERE c.sandbox_id=s.id) AS no_slot,
                    NOT EXISTS (SELECT 1 FROM sandbox_artifact_refs r WHERE r.sandbox_id=s.id) AS no_ref
                    FROM sandboxes s WHERE id=%s""",
                        (request.ref.id,),
                    )
                ).fetchone()
        if row is None:
            return True
        return bool(
            row["consumer_id"] == request.principal.consumer_id
            and row["installation_id"] == request.identity.installation_id
            and row["host_id"] == request.identity.host_id
            and row["generation"] == request.ref.generation
            and row["spec"] == spec_wire(request.spec).model_dump(mode="json")
            and row["snapshot"]
            == SnapshotWire.from_domain(request.profile_snapshot).model_dump(mode="json")
            and row["accepted_at"] == request.accepted_at
            and row["absolute_deadline"] == request.absolute_deadline
            and row["cleaned_at"] is not None
            and row["state"] in ("terminated", "failed")
            and not row["cleanup_pending"]
            and row["no_slot"]
            and row["no_ref"]
        )

    async def start_allowed(self, request: EnsureRequest, boot_id: UUID) -> bool:
        async with asyncio.timeout(self.timeout):
            async with self.pool.connection() as conn, conn.transaction():
                return await self._allowed(conn, request, boot_id)

    async def run_start_if_allowed(
        self, request: EnsureRequest, boot_id: UUID, start: Callable[[], Awaitable[None]]
    ) -> bool:
        # Only this bounded callback runs under the row lock. Create/attach never do.
        # A lost connection/cancelled callback is uncertain, never proof of no side effect.
        async with asyncio.timeout(self.timeout):
            async with self.pool.connection() as conn, conn.transaction():
                if not await self._allowed(conn, request, boot_id):
                    return False
                await start()
                return True
