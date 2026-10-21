"""Transactional lifecycle authority, expressed through mapped SQLModel records."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import psycopg
from sqlalchemy import delete, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from boxie.application.admission import (
    AdmissionReadiness,
    DependencyCheck,
)
from boxie.application.ports import AdmissionProfileResolver
from boxie.contracts.consumer import SpecWire, ViewWire, spec_wire
from boxie.contracts.execution import SnapshotWire
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import SandboxRequest, SupervisorObservation
from boxie.domain.models import (
    Principal,
    SandboxSpec,
    SandboxView,
    SupervisorIdentity,
    TerminationReason,
)
from boxie.observability.events import DiagnosticSink, Transition, deliver, emit
from boxie.observability.telemetry.context import current_request_id
from boxie.persistence.database import SessionFactory
from boxie.persistence.models import (
    Artifact,
    CapacitySlot,
    Outbox,
    Sandbox,
    SandboxArtifactRef,
    SupervisorObservationRecord,
)


@asynccontextmanager
async def consumer_session(sessions: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Translate availability failures through commit acknowledgement; never retry."""
    try:
        async with sessions() as session, session.begin():
            yield session
    except PoolTimeout:
        raise BoxieError(503, "persistence unavailable") from None
    except DBAPIError as error:
        original = error.orig
        if isinstance(original, psycopg.OperationalError):
            state = original.sqlstate
            if (
                (type(original) is psycopg.OperationalError and state is None)
                or (state is not None and state.startswith(("08", "53")))
                or state in {"40001", "40P01", "55P03", "57014", "57P01", "57P02", "57P03"}
            ):
                raise BoxieError(503, "persistence unavailable") from None
        raise


def view(row: Sandbox) -> SandboxView:
    return ViewWire(
        id=row.id,
        state=row.state,
        created_at=row.accepted_at,
        absolute_deadline=row.absolute_deadline,
        reason=row.reason,
        cleanup_pending=row.cleanup_pending,
    ).domain()


async def keyed_lock(session: AsyncSession, key: str) -> None:
    # Collisions only serialize unrelated keys; unique constraints remain authoritative.
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
    await session.exec(select(func.pg_advisory_xact_lock(number)))


class PostgresLifecycle:
    def __init__(
        self,
        sessions: SessionFactory,
        resolver: AdmissionProfileResolver,
        identity: SupervisorIdentity,
        dependencies: DependencyCheck,
        readiness: AdmissionReadiness | None = None,
        *,
        diagnostics: DiagnosticSink = emit,
    ) -> None:
        self.sessions, self.resolver, self.identity = sessions, resolver, identity
        self.diagnostics = diagnostics
        self.dependencies, self.readiness = dependencies, readiness or AdmissionReadiness()

    async def _ready(self, session: AsyncSession) -> bool:
        backlog, oldest = (
            await session.exec(
                select(func.count(col(Outbox.id)), func.min(col(Outbox.created_at))).where(
                    col(Outbox.delivered_at).is_(None), col(Outbox.kind).in_(["start", "stop"])
                )
            )
        ).one()
        now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
        age = (now - oldest).total_seconds() if oldest else 0
        if backlog >= self.readiness.max_backlog or age > self.readiness.oldest_command_seconds:
            return False
        try:
            async with asyncio.timeout(self.readiness.dependency_timeout_seconds):
                return await self.dependencies()
        except Exception:
            return False

    async def ready(self) -> bool:
        try:
            async with asyncio.timeout(self.readiness.dependency_timeout_seconds + 1):
                async with self.sessions() as session:
                    return await self._ready(session)
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
            async with consumer_session(self.sessions) as session:
                await keyed_lock(session, json.dumps([principal.consumer_id, idempotency_key]))
                old = (
                    await session.exec(
                        select(Sandbox).where(
                            Sandbox.consumer_id == principal.consumer_id,
                            Sandbox.idempotency_key == idempotency_key,
                        )
                    )
                ).one_or_none()
                if old:
                    if old.request_hash != digest:
                        raise BoxieError(409, "idempotency key reused for different request")
                    return view(old)
                if not await self._ready(session):
                    raise BoxieError(503, "orchestration not ready for new admission")
                async with asyncio.timeout(self.readiness.dependency_timeout_seconds):
                    snapshot = await self.resolver.resolve_admission(spec)
                artifact = (
                    await session.exec(
                        select(Artifact).where(Artifact.id == spec.artifact_id).with_for_update()
                    )
                ).one_or_none()
                if artifact is None or artifact.consumer_id != principal.consumer_id:
                    raise BoxieError(404, "artifact not found")
                if artifact.retired:
                    raise BoxieError(409, "artifact is retired")
                slot = (
                    await session.exec(
                        select(CapacitySlot)
                        .where(col(CapacitySlot.sandbox_id).is_(None))
                        .order_by(col(CapacitySlot.slot))
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                ).one_or_none()
                if slot is None:
                    raise BoxieError(429, "sandbox capacity reached")
                accepted = cast(datetime, await session.scalar(select(func.clock_timestamp())))
                identifier = uuid4()
                row = Sandbox(
                    id=identifier,
                    consumer_id=principal.consumer_id,
                    idempotency_key=idempotency_key,
                    request_hash=digest,
                    creation_request_id=UUID(current_request_id() or str(uuid4())),
                    spec=normalized,
                    snapshot=SnapshotWire.from_domain(snapshot).model_dump(mode="json"),
                    installation_id=self.identity.installation_id,
                    host_id=self.identity.host_id,
                    accepted_at=accepted,
                    absolute_deadline=accepted + timedelta(seconds=spec.policy.max_seconds),
                )
                session.add(row)
                await session.flush()
                slot.sandbox_id = identifier
                session.add(SandboxArtifactRef(sandbox_id=identifier, artifact_id=spec.artifact_id))
                await self._enqueue(
                    session, identifier, "start", request_id=row.creation_request_id
                )
            deliver(
                self.diagnostics,
                Transition(
                    event="sandbox.accepted",
                    sandbox_id=identifier,
                    request_id=row.creation_request_id,
                ),
            )
            return view(row)
        except TimeoutError:
            raise BoxieError(503, "admission dependency timeout") from None

    @staticmethod
    async def _enqueue(
        session: AsyncSession,
        identifier: UUID,
        kind: str,
        *,
        request_id: UUID | None,
        key: str | None = None,
    ) -> None:
        # Callers hold the sandbox row/admission lock, serializing the unique key.
        deduplication_key = key or f"{identifier}:{kind}"
        if (
            await session.exec(
                select(Outbox.id).where(Outbox.deduplication_key == deduplication_key)
            )
        ).first() is None:
            session.add(
                Outbox(
                    id=uuid4(),
                    deduplication_key=deduplication_key,
                    sandbox_id=identifier,
                    kind=kind,
                    request_id=request_id,
                )
            )

    async def get(self, principal: Principal, sandbox_id: UUID) -> SandboxView:
        async with consumer_session(self.sessions) as session:
            row = await session.get(Sandbox, sandbox_id)
            if row is None or row.consumer_id != principal.consumer_id:
                raise BoxieError(404, "sandbox not found")
            return view(row)

    async def terminate(
        self, principal: Principal, sandbox_id: UUID, reason: TerminationReason
    ) -> SandboxView:
        async with consumer_session(self.sessions) as session:
            row = (
                await session.exec(
                    select(Sandbox)
                    .where(Sandbox.id == sandbox_id, Sandbox.consumer_id == principal.consumer_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise BoxieError(404, "sandbox not found")
            if row.cleaned_at is not None or row.desired_stop:
                return view(row)
            was_pending = row.cleanup_pending
            row.reason = row.reason or reason.value
            if row.stop_request_id is None:
                row.stop_request_id = (
                    row.creation_request_id
                    if row.reason == TerminationReason.PROVISIONING_FAILED.value
                    else UUID(current_request_id() or str(uuid4()))
                )
            row.desired_stop = True
            row.state, row.cleanup_pending = "terminating", True
            if not was_pending:
                row.cleanup_pending_since = cast(
                    datetime, await session.scalar(select(func.clock_timestamp()))
                )
            await self._enqueue(session, sandbox_id, "stop", request_id=row.stop_request_id)
        deliver(
            self.diagnostics,
            Transition(
                event="termination.requested",
                sandbox_id=sandbox_id,
                reason=row.reason,
                request_id=row.stop_request_id,
            ),
        )
        return view(row)

    async def load_request(self, sandbox_id: UUID) -> SandboxRequest:
        """Activity-side lookup only: spec/snapshot may contain secrets."""
        async with self.sessions() as session:
            row = await session.get(Sandbox, sandbox_id)
            if row is None:
                raise BoxieError(404, "sandbox not found")
            return SandboxRequest(
                sandbox_id,
                Principal(row.consumer_id),
                SupervisorIdentity(row.installation_id, row.host_id),
                row.generation,
                row.supervisor_boot_id,
                SpecWire.model_validate(row.spec).domain(),
                SnapshotWire.model_validate(row.snapshot).domain(),
                row.accepted_at,
                row.absolute_deadline,
                row.desired_stop,
                TerminationReason(row.reason) if row.reason else None,
            )

    async def bind_supervisor_boot(
        self,
        principal: Principal,
        identity: SupervisorIdentity,
        sandbox_id: UUID,
        generation: int,
        expected_boot: UUID | None,
        boot_id: UUID,
    ) -> bool:
        """Only a trusted current-boot RPC can authorize this fenced transition."""
        async with self.sessions() as session, session.begin():
            row = (
                await session.exec(
                    select(Sandbox)
                    .where(
                        Sandbox.id == sandbox_id,
                        Sandbox.consumer_id == principal.consumer_id,
                        Sandbox.installation_id == identity.installation_id,
                        Sandbox.host_id == identity.host_id,
                        Sandbox.generation == generation,
                        col(Sandbox.supervisor_boot_id).is_not_distinct_from(expected_boot),
                        col(Sandbox.cleaned_at).is_(None),
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return False
            row.supervisor_boot_id = boot_id
            if expected_boot != boot_id:
                await session.exec(
                    delete(SupervisorObservationRecord).where(
                        col(SupervisorObservationRecord.sandbox_id) == sandbox_id
                    )
                )
            return True

    async def record_observation(self, observation: SupervisorObservation) -> bool:
        """Project a fresh authenticated report, releasing only proven absence."""
        item = observation
        async with self.sessions() as session, session.begin():
            row = (
                await session.exec(
                    select(Sandbox)
                    .where(
                        Sandbox.id == item.sandbox_id,
                        Sandbox.consumer_id == item.principal.consumer_id,
                        Sandbox.installation_id == item.identity.installation_id,
                        Sandbox.host_id == item.identity.host_id,
                        Sandbox.generation == item.generation,
                        Sandbox.supervisor_boot_id == item.boot_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None or row.cleaned_at is not None:
                return False
            old = await session.get(SupervisorObservationRecord, item.sandbox_id)
            if old is not None and old.event_version >= item.event_version:
                return False
            prior_ready = old is not None and old.ready_at is not None
            record = SupervisorObservationRecord(
                sandbox_id=item.sandbox_id,
                generation=item.generation,
                boot_id=item.boot_id,
                event_version=item.event_version,
                observed_at=item.observed_at,
                ready_at=item.ready_at,
                last_input_at=item.last_input_at,
                disconnected_at=item.disconnected_at,
                resource_present=item.resource_present,
                removal_confirmed=item.removal_confirmed,
                cleanup_pending=item.cleanup_pending,
                attachment_epoch=item.attachment_epoch,
                absolute_deadline=item.absolute_deadline,
                control_lease_until=item.control_lease_until,
                container_id=item.container_id,
            )
            await session.merge(record)
            row.state = (
                "terminating"
                if row.desired_stop and not item.removal_confirmed
                else item.state.value
            )
            row.reason = row.reason or (item.reason.value if item.reason else None)
            # The supervisor can finish autonomous removal before SQL receives a stop command.
            if not row.desired_stop and row.stop_request_id is None and item.reason is not None:
                row.stop_request_id = (
                    row.creation_request_id
                    if row.reason == TerminationReason.PROVISIONING_FAILED.value
                    else UUID(current_request_id() or str(uuid4()))
                )
            was_pending = row.cleanup_pending
            row.cleanup_pending = not item.removal_confirmed and (
                item.cleanup_pending
                or row.desired_stop
                or item.state.value in ("terminating", "terminated", "failed")
            )
            row.cleaned_at = (
                cast(datetime, await session.scalar(select(func.clock_timestamp())))
                if item.removal_confirmed
                else None
            )
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            if row.cleanup_pending and not was_pending:
                row.cleanup_pending_since = now
            events: list[Transition] = []
            if row.provisioning_outcome is None:
                outcome = (
                    "ready"
                    if item.ready_at is not None or prior_ready
                    else "failed"
                    if item.removal_confirmed and row.reason == "provisioning_failed"
                    else "stopped"
                    if item.removal_confirmed
                    else None
                )
                if outcome is not None:
                    row.provisioning_outcome = outcome
                    duration = None
                    if outcome == "ready":
                        duration = max(0, (now - row.accepted_at).total_seconds())
                    events.append(
                        Transition(
                            event="provisioning.completed",
                            request_id=row.creation_request_id,
                            sandbox_id=item.sandbox_id,
                            outcome=outcome,
                            duration_seconds=duration,
                        )
                    )
                    if outcome == "ready":
                        events.append(
                            Transition(
                                event="sandbox.ready",
                                sandbox_id=item.sandbox_id,
                                request_id=row.creation_request_id,
                            )
                        )
            if item.removal_confirmed:
                slot = (
                    await session.exec(
                        select(CapacitySlot).where(CapacitySlot.sandbox_id == item.sandbox_id)
                    )
                ).one_or_none()
                if slot is not None:
                    slot.sandbox_id = None
                reference = await session.get(SandboxArtifactRef, item.sandbox_id)
                if reference is not None:
                    await session.delete(reference)
                events.append(
                    Transition(
                        event="cleanup.completed",
                        sandbox_id=item.sandbox_id,
                        request_id=row.stop_request_id,
                    )
                )
        deliver(self.diagnostics, *events)
        return True

    async def purge_retained(self) -> int:
        """Keep retry identity seven days after cleanup; never age out ownership."""
        async with self.sessions() as session, session.begin():
            cutoff = func.clock_timestamp() - timedelta(days=7)
            await session.exec(delete(Outbox).where(col(Outbox.delivered_at) < cutoff))
            rows = (
                await session.exec(
                    select(Sandbox)
                    .where(
                        col(Sandbox.cleaned_at) < cutoff,
                        col(Sandbox.cleanup_pending).is_(False),
                        ~select(SandboxArtifactRef)
                        .where(SandboxArtifactRef.sandbox_id == Sandbox.id)
                        .exists(),
                        ~select(CapacitySlot).where(CapacitySlot.sandbox_id == Sandbox.id).exists(),
                        ~select(Outbox).where(Outbox.sandbox_id == Sandbox.id).exists(),
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1000)
                )
            ).all()
            for row in rows:
                observation = await session.get(SupervisorObservationRecord, row.id)
                if observation:
                    await session.delete(observation)
                    await session.flush()
                await session.delete(row)
        return len(rows)
