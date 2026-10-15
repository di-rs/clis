"""Short transactional SKIP LOCKED claims; every acknowledgment is owner fenced."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from boxie.persistence.postgres import Pool


@dataclass(frozen=True)
class OutboxEvent:
    id: UUID
    sandbox_id: UUID
    lease_owner: str
    lease_expires_at: datetime
    attempts: int


class PostgresOutbox:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def claim(self, owner: str) -> list[OutboxEvent]:
        async with self.pool.connection() as conn, conn.transaction():
            rows = await (
                await conn.execute(
                    """WITH pending AS (
                SELECT id FROM outbox WHERE delivered_at IS NULL
                AND next_attempt_at <= clock_timestamp()
                AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 32)
                UPDATE outbox o SET lease_owner=%s,
                lease_expires_at=clock_timestamp()+interval '30 seconds',attempts=attempts+1
                FROM pending WHERE o.id=pending.id
                RETURNING o.id,o.sandbox_id,o.lease_owner,o.lease_expires_at,o.attempts""",
                    (owner,),
                )
            ).fetchall()
            return [OutboxEvent(**row) for row in rows]

    async def valid(self, event: OutboxEvent) -> bool:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """SELECT id FROM outbox WHERE id=%s
                AND lease_owner=%s AND lease_expires_at>clock_timestamp()+interval '5 seconds'
                AND delivered_at IS NULL""",
                    (event.id, event.lease_owner),
                )
            ).fetchone()
            return row is not None

    async def acknowledge(self, event: OutboxEvent) -> bool:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """UPDATE outbox SET delivered_at=clock_timestamp(),
                lease_owner=NULL,lease_expires_at=NULL WHERE id=%s AND lease_owner=%s
                AND lease_expires_at>clock_timestamp() AND delivered_at IS NULL RETURNING id""",
                    (event.id, event.lease_owner),
                )
            ).fetchone()
            return row is not None

    async def retry(self, event: OutboxEvent) -> None:
        delay = min(30, 2 ** min(5, event.attempts - 1))
        async with self.pool.connection() as conn:
            await conn.execute(
                """UPDATE outbox SET lease_owner=NULL,lease_expires_at=NULL,
                next_attempt_at=clock_timestamp()+%s*interval '1 second'
                WHERE id=%s AND lease_owner=%s AND lease_expires_at>clock_timestamp()
                AND delivered_at IS NULL""",
                (delay, event.id, event.lease_owner),
            )

    async def confirmed_terminal(self, sandbox_id: UUID) -> bool:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """SELECT id FROM sandboxes s WHERE id=%s
                AND cleaned_at IS NOT NULL AND NOT cleanup_pending
                AND NOT EXISTS(SELECT 1 FROM capacity_slots c WHERE c.sandbox_id=s.id)
                AND NOT EXISTS(SELECT 1 FROM sandbox_artifact_refs r WHERE r.sandbox_id=s.id)""",
                    (sandbox_id,),
                )
            ).fetchone()
            return row is not None
