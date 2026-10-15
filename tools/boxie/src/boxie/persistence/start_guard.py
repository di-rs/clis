"""Agent-owned final-start guard sharing durable stop's sandbox row lock."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import psycopg

from boxie.domain.lifecycle import EnsureRequest
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
