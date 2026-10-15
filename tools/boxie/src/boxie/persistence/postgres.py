"""Transactional lifecycle authority; runtime work is exclusively durable outbox work."""

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from boxie.application.durable_lifecycle import (
    AdmissionProfileResolver,
    AdmissionReadiness,
    DependencyCheck,
)
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import AgentObservation, DurableRequest
from boxie.domain.models import (
    AgentIdentity,
    Principal,
    SandboxSpec,
    SandboxView,
    TerminationReason,
)
from boxie.sandbox.protocol import SnapshotWire, SpecWire, ViewWire, spec_wire

Row = dict[str, Any]
Pool = AsyncConnectionPool[psycopg.AsyncConnection[Row]]


def view(row: Row) -> SandboxView:
    return ViewWire(
        id=row["id"],
        state=row["state"],
        created_at=row["accepted_at"],
        absolute_deadline=row["absolute_deadline"],
        reason=row["reason"],
        cleanup_pending=row["cleanup_pending"],
    ).domain()


async def keyed_lock(conn: psycopg.AsyncConnection[Row], key: str) -> None:
    # Hash collisions only serialize unrelated keys; uniqueness remains SQL-owned.
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
    await conn.execute("SET LOCAL lock_timeout = '10s'")
    await conn.execute("SELECT pg_advisory_xact_lock(%s)", (number,))


class PostgresLifecycle:
    def __init__(
        self,
        pool: Pool,
        resolver: AdmissionProfileResolver,
        identity: AgentIdentity,
        dependencies: DependencyCheck,
        readiness: AdmissionReadiness | None = None,
    ) -> None:
        self.pool, self.resolver, self.identity = pool, resolver, identity
        self.dependencies, self.readiness = dependencies, readiness or AdmissionReadiness()

    async def _ready(self, conn: psycopg.AsyncConnection[Row]) -> bool:
        row = await (
            await conn.execute("""SELECT count(*) AS backlog,
            COALESCE(EXTRACT(EPOCH FROM clock_timestamp() - min(created_at)), 0) AS age
            FROM outbox WHERE delivered_at IS NULL""")
        ).fetchone()
        assert row is not None
        if (
            row["backlog"] >= self.readiness.max_backlog
            or row["age"] > self.readiness.oldest_command_seconds
        ):
            return False
        try:
            async with asyncio.timeout(self.readiness.dependency_timeout_seconds):
                return await self.dependencies()
        except Exception:
            return False

    async def ready(self) -> bool:
        try:
            async with asyncio.timeout(self.readiness.dependency_timeout_seconds + 1):
                async with self.pool.connection() as conn:
                    return await self._ready(conn)
        except Exception:
            return False

    async def submit(
        self, principal: Principal, idempotency_key: str, spec: SandboxSpec
    ) -> SandboxView:
        if not 1 <= len(idempotency_key) <= 200:
            raise BoxieError(422, "invalid idempotency key")
        normalized = spec_wire(spec).model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await keyed_lock(conn, json.dumps([principal.consumer_id, idempotency_key]))
                old = await (
                    await conn.execute(
                        "SELECT * FROM sandboxes WHERE consumer_id=%s AND idempotency_key=%s",
                        (principal.consumer_id, idempotency_key),
                    )
                ).fetchone()
                if old:
                    if old["request_hash"] != digest:
                        raise BoxieError(409, "idempotency key reused for different request")
                    return view(old)
                if not await self._ready(conn):
                    raise BoxieError(503, "orchestration not ready for new admission")
                async with asyncio.timeout(self.readiness.dependency_timeout_seconds):
                    snapshot = await self.resolver.resolve_admission(spec)
                artifact = await (
                    await conn.execute(
                        "SELECT * FROM artifacts WHERE id=%s FOR UPDATE", (spec.artifact_id,)
                    )
                ).fetchone()
                if artifact is None or artifact["consumer_id"] != principal.consumer_id:
                    raise BoxieError(404, "artifact not found")
                if artifact["retired"]:
                    raise BoxieError(409, "artifact is retired")
                slot = await (
                    await conn.execute(
                        "SELECT slot FROM capacity_slots WHERE sandbox_id IS NULL ORDER BY slot FOR UPDATE SKIP LOCKED LIMIT 1"
                    )
                ).fetchone()
                if slot is None:
                    raise BoxieError(429, "sandbox capacity reached")
                timestamp = await (await conn.execute("SELECT clock_timestamp() AS now")).fetchone()
                assert timestamp is not None
                accepted = timestamp["now"]
                identifier = uuid4()
                row = await (
                    await conn.execute(
                        """INSERT INTO sandboxes
                    (id,consumer_id,idempotency_key,request_hash,spec,snapshot,installation_id,host_id,
                     accepted_at,absolute_deadline) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (
                            identifier,
                            principal.consumer_id,
                            idempotency_key,
                            digest,
                            Jsonb(normalized),
                            Jsonb(SnapshotWire.from_domain(snapshot).model_dump(mode="json")),
                            self.identity.installation_id,
                            self.identity.host_id,
                            accepted,
                            accepted + timedelta(seconds=spec.policy.max_seconds),
                        ),
                    )
                ).fetchone()
                await conn.execute(
                    "UPDATE capacity_slots SET sandbox_id=%s WHERE slot=%s",
                    (identifier, slot["slot"]),
                )
                await conn.execute(
                    "INSERT INTO sandbox_artifact_refs VALUES (%s,%s)",
                    (identifier, spec.artifact_id),
                )
                await self._enqueue(conn, identifier, "start")
                assert row is not None
                return view(row)
        except TimeoutError, psycopg.errors.LockNotAvailable:
            raise BoxieError(503, "admission dependency timeout") from None

    @staticmethod
    async def _enqueue(conn: psycopg.AsyncConnection[Row], identifier: UUID, kind: str) -> None:
        await conn.execute(
            """INSERT INTO outbox (id,deduplication_key,sandbox_id,kind)
            VALUES (%s,%s,%s,%s) ON CONFLICT (deduplication_key) DO NOTHING""",
            (uuid4(), f"{identifier}:{kind}", identifier, kind),
        )

    async def get(self, principal: Principal, sandbox_id: UUID) -> SandboxView:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM sandboxes WHERE id=%s AND consumer_id=%s",
                    (sandbox_id, principal.consumer_id),
                )
            ).fetchone()
            if row is None:
                raise BoxieError(404, "sandbox not found")
            return view(row)

    async def terminate(
        self, principal: Principal, sandbox_id: UUID, reason: TerminationReason
    ) -> SandboxView:
        async with self.pool.connection() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    "SELECT * FROM sandboxes WHERE id=%s AND consumer_id=%s FOR UPDATE",
                    (sandbox_id, principal.consumer_id),
                )
            ).fetchone()
            if row is None:
                raise BoxieError(404, "sandbox not found")
            if row["cleaned_at"] is not None or row["desired_stop"]:
                return view(row)
            row = await (
                await conn.execute(
                    """UPDATE sandboxes SET desired_stop=true,
                reason=%s,state='terminating',cleanup_pending=true WHERE id=%s RETURNING *""",
                    (reason.value, sandbox_id),
                )
            ).fetchone()
            await self._enqueue(conn, sandbox_id, "stop")
            assert row is not None
            return view(row)

    async def load_request(self, sandbox_id: UUID) -> DurableRequest:
        """Activity-side lookup only: spec/snapshot may contain secrets."""
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("SELECT * FROM sandboxes WHERE id=%s", (sandbox_id,))
            ).fetchone()
            if row is None:
                raise BoxieError(404, "sandbox not found")
            return DurableRequest(
                sandbox_id,
                Principal(row["consumer_id"]),
                AgentIdentity(row["installation_id"], row["host_id"]),
                row["generation"],
                row["agent_boot_id"],
                SpecWire.model_validate(row["spec"]).domain(),
                SnapshotWire.model_validate(row["snapshot"]).domain(),
                row["accepted_at"],
                row["absolute_deadline"],
                row["desired_stop"],
                TerminationReason(row["reason"]) if row["reason"] else None,
            )

    async def bind_agent_boot(
        self,
        principal: Principal,
        identity: AgentIdentity,
        sandbox_id: UUID,
        generation: int,
        expected_boot: UUID | None,
        boot_id: UUID,
    ) -> bool:
        """Trusted caller must authenticate the current agent boot before this CAS.

        Boot observations are never evidence of a boot transition on their own.
        Callers handling a restart must reconcile absence before confirming removal.
        """
        async with self.pool.connection() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    """UPDATE sandboxes SET agent_boot_id=%s
                WHERE id=%s AND consumer_id=%s AND installation_id=%s AND host_id=%s
                AND generation=%s AND agent_boot_id IS NOT DISTINCT FROM %s
                AND cleaned_at IS NULL RETURNING id""",
                    (
                        boot_id,
                        sandbox_id,
                        principal.consumer_id,
                        identity.installation_id,
                        identity.host_id,
                        generation,
                        expected_boot,
                    ),
                )
            ).fetchone()
            if row and expected_boot != boot_id:
                await conn.execute(
                    "DELETE FROM agent_observations WHERE sandbox_id=%s", (sandbox_id,)
                )
            return row is not None

    async def record_observation(self, observation: AgentObservation) -> bool:
        """Atomically project a fresh authenticated report, releasing only proven absence."""
        item = observation
        async with self.pool.connection() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    """SELECT * FROM sandboxes WHERE id=%s
                AND consumer_id=%s AND installation_id=%s AND host_id=%s
                AND generation=%s AND agent_boot_id=%s FOR UPDATE""",
                    (
                        item.sandbox_id,
                        item.principal.consumer_id,
                        item.identity.installation_id,
                        item.identity.host_id,
                        item.generation,
                        item.boot_id,
                    ),
                )
            ).fetchone()
            if row is None or row["cleaned_at"] is not None:
                return False
            old = await (
                await conn.execute(
                    "SELECT event_version FROM agent_observations WHERE sandbox_id=%s",
                    (item.sandbox_id,),
                )
            ).fetchone()
            if old is not None and old["event_version"] >= item.event_version:
                return False
            await conn.execute(
                """INSERT INTO agent_observations
                (sandbox_id,generation,boot_id,event_version,observed_at,ready_at,last_input_at,
                 disconnected_at,resource_present,removal_confirmed,cleanup_pending)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (sandbox_id) DO UPDATE SET generation=EXCLUDED.generation,
                boot_id=EXCLUDED.boot_id,event_version=EXCLUDED.event_version,
                observed_at=EXCLUDED.observed_at,ready_at=EXCLUDED.ready_at,
                last_input_at=EXCLUDED.last_input_at,disconnected_at=EXCLUDED.disconnected_at,
                resource_present=EXCLUDED.resource_present,removal_confirmed=EXCLUDED.removal_confirmed,cleanup_pending=EXCLUDED.cleanup_pending""",
                (
                    item.sandbox_id,
                    item.generation,
                    item.boot_id,
                    item.event_version,
                    item.observed_at,
                    item.ready_at,
                    item.last_input_at,
                    item.disconnected_at,
                    item.resource_present,
                    item.removal_confirmed,
                    item.cleanup_pending,
                ),
            )
            state = (
                "terminating"
                if row["desired_stop"] and not item.removal_confirmed
                else item.state.value
            )
            await conn.execute(
                """UPDATE sandboxes SET state=%s,reason=COALESCE(reason,%s),
                cleanup_pending=%s,cleaned_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END
                WHERE id=%s""",
                (
                    state,
                    item.reason.value if item.reason else None,
                    not item.removal_confirmed
                    and (
                        item.cleanup_pending
                        or row["desired_stop"]
                        or item.state.value in ("terminating", "terminated", "failed")
                    ),
                    item.removal_confirmed,
                    item.sandbox_id,
                ),
            )
            if item.removal_confirmed:
                await conn.execute(
                    "UPDATE capacity_slots SET sandbox_id=NULL WHERE sandbox_id=%s",
                    (item.sandbox_id,),
                )
                await conn.execute(
                    "DELETE FROM sandbox_artifact_refs WHERE sandbox_id=%s", (item.sandbox_id,)
                )
            return True

    async def confirm_removed(self, observation: AgentObservation) -> bool:
        if not observation.removal_confirmed:
            raise ValueError("explicit physical removal confirmation required")
        return await self.record_observation(observation)

    async def purge_retained(self) -> None:
        """Keep retry identity for seven days after cleanup; never age out ownership."""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM outbox WHERE delivered_at < clock_timestamp()-interval '7 days'"
            )
            rows = await (
                await conn.execute("""SELECT id FROM sandboxes s WHERE
                cleaned_at < clock_timestamp()-interval '7 days' AND NOT cleanup_pending
                AND NOT EXISTS (SELECT 1 FROM sandbox_artifact_refs r WHERE r.sandbox_id=s.id)
                AND NOT EXISTS (SELECT 1 FROM capacity_slots c WHERE c.sandbox_id=s.id)
                AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.sandbox_id=s.id)
                FOR UPDATE SKIP LOCKED LIMIT 1000""")
            ).fetchall()
            for row in rows:
                await conn.execute(
                    "DELETE FROM agent_observations WHERE sandbox_id=%s", (row["id"],)
                )
                await conn.execute("DELETE FROM sandboxes WHERE id=%s", (row["id"],))
