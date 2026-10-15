"""Persistent PR1 admission authority and deterministic sandbox ownership."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field

from boxie.bootstrap.config import AgentConfig
from boxie.domain.errors import BoxieError
from boxie.domain.models import (
    AgentIdentity,
    Principal,
    ProfileSnapshot,
    SandboxSpec,
    SandboxState,
    SandboxView,
    TerminationReason,
)
from boxie.domain.policy import validate_policy
from boxie.persistence.artifacts import ArtifactStore
from boxie.persistence.atomic import atomic_write
from boxie.sandbox.docker import DockerPort, container_config
from boxie.sandbox.profiles import ProfileRegistry
from boxie.sandbox.protocol import IdentityWire, SnapshotWire, SpecWire, WireModel, spec_wire
from boxie.sandbox.terminal import Terminal

logger = logging.getLogger(__name__)
FINAL = {SandboxState.TERMINATED, SandboxState.FAILED}


class Record(WireModel):
    id: UUID
    consumer_id: str
    idempotency_key: str
    fingerprint: str
    spec: SpecWire
    snapshot: SnapshotWire
    identity: IdentityWire
    generation: int = 1
    created_at: datetime
    absolute_deadline: datetime
    idle_deadline: datetime | None = None
    disconnected_at: datetime | None = None
    state: SandboxState = SandboxState.ACCEPTED
    reason: TerminationReason | None = None
    cleanup_pending: bool = False
    creation_uncertain: bool = False
    output_bytes: int = 0

    def view(self) -> SandboxView:
        return SandboxView(
            self.id,
            self.state,
            self.created_at,
            self.absolute_deadline,
            self.reason,
            self.cleanup_pending,
        )

    @property
    def name(self) -> str:
        return f"boxie-{self.identity.installation_id.hex}-{self.id.hex}-{self.generation}"

    @property
    def principal(self) -> Principal:
        return Principal(self.consumer_id)

    @property
    def labels(self) -> dict[str, str]:
        return {
            "boxie.installation": str(self.identity.installation_id),
            "boxie.host": self.identity.host_id,
            "boxie.sandbox": str(self.id),
            "boxie.generation": str(self.generation),
            "boxie.consumer": self.consumer_id,
            "boxie.deadline": self.absolute_deadline.isoformat(),
            "boxie.artifact": str(self.spec.artifact_id),
        }


class RegistryFile(WireModel):
    identity: IdentityWire
    records: list[Record] = Field(default_factory=list)


@dataclass
class LiveDeadlines:
    """Process-local elapsed timers; restart always reconciles persisted records."""

    absolute: float
    idle: float | None = None
    disconnect: float | None = None


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        docker: DockerPort,
        artifacts: ArtifactStore,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
    ) -> None:
        self.config, self.docker, self.artifacts, self.clock = config, docker, artifacts, clock
        self.monotonic = monotonic
        self._deadlines: dict[UUID, LiveDeadlines] = {}
        self.profiles = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
        self.path = config.data_dir / "registry.json"
        self.identity = IdentityWire(
            installation_id=config.identity.installation_id, host_id=config.identity.host_id
        )
        self.records: dict[UUID, Record] = {}
        if self.path.exists():
            saved = RegistryFile.model_validate_json(self.path.read_bytes())
            if saved.identity != self.identity:
                raise ValueError("agent installation identity differs from persisted registry")
            self.records = {record.id: record for record in saved.records}
        self._admission = asyncio.Lock()
        self._operations: dict[UUID, asyncio.Lock] = {}
        self._workers: dict[UUID, asyncio.Task[None]] = {}
        self._cleanup_tasks: dict[UUID, asyncio.Task[SandboxView]] = {}
        self.terminals: dict[UUID, Terminal] = {}
        self._sweeper: asyncio.Task[None] | None = None
        self._stopping = False
        self._save()

    def _save(self) -> None:
        atomic_write(
            self.path,
            RegistryFile(identity=self.identity, records=list(self.records.values()))
            .model_dump_json()
            .encode(),
        )

    @property
    def reserved_count(self) -> int:
        return sum(record.state not in FINAL for record in self.records.values())

    def artifact_exists(self, artifact_id: str | UUID) -> bool:
        return self.artifacts.exists(UUID(str(artifact_id)))

    def resolve_profile(
        self, profile_id: str, command: tuple[str, ...], env: Mapping[str, str]
    ) -> ProfileSnapshot:
        return self.profiles.resolve_profile(profile_id, command, env)

    def owned(self, principal: Principal, sandbox_id: UUID) -> Record:
        record = self.records.get(sandbox_id)
        if record is None or record.consumer_id != principal.consumer_id:
            raise BoxieError(404, "sandbox not found")
        return record

    def _controlled(self, identity: AgentIdentity, sandbox_id: UUID, generation: int) -> Record:
        record = self.records.get(sandbox_id)
        if record is None or identity != self.config.identity or generation != record.generation:
            raise BoxieError(404, "sandbox generation not found")
        return record

    async def submit(
        self, principal: Principal, idempotency_key: str, spec: SandboxSpec
    ) -> SandboxView:
        # Wire validation also applies to direct callers and canonical fingerprints.
        body = spec_wire(spec)
        validate_policy(spec.policy, self.config.policy_maximum)
        fingerprint = hashlib.sha256(
            json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        async with self._admission:
            if self._stopping:
                raise BoxieError(503, "agent is stopping")
            for record in self.records.values():
                if (
                    record.consumer_id == principal.consumer_id
                    and record.idempotency_key == idempotency_key
                ):
                    if record.fingerprint != fingerprint:
                        raise BoxieError(409, "idempotency key reused with a different request")
                    return record.view()
            snapshot = self.resolve_profile(spec.profile_id, spec.command, spec.env)
            # Check retirement before capacity so retired artifacts cannot be resurrected.
            self.artifacts.path(principal, spec.artifact_id)
            if self.reserved_count >= self.config.max_sandboxes:
                raise BoxieError(429, "sandbox capacity exhausted")
            now = self.clock()
            record = Record(
                id=uuid4(),
                consumer_id=principal.consumer_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                spec=body,
                snapshot=SnapshotWire.from_domain(snapshot),
                identity=self.identity,
                created_at=now,
                absolute_deadline=now + timedelta(seconds=spec.policy.max_seconds),
            )
            # Record intent first. No await may separate intent/reference persistence from
            # scheduling Docker; restart handles any crash in this sequence conservatively.
            self.records[record.id] = record
            self._deadlines[record.id] = LiveDeadlines(self.monotonic() + spec.policy.max_seconds)
            self._save()
            try:
                self.artifacts.retain(principal, spec.artifact_id, record.name)
            except Exception:
                del self.records[record.id]
                del self._deadlines[record.id]
                self._save()
                raise
            self._workers[record.id] = asyncio.create_task(self._provision(record))
            return record.view()

    async def get(self, principal: Principal, sandbox_id: UUID) -> SandboxView:
        return self.owned(principal, sandbox_id).view()

    async def inspect(
        self, identity: AgentIdentity, sandbox_id: UUID, generation: int
    ) -> SandboxView:
        return self._controlled(identity, sandbox_id, generation).view()

    async def ensure(
        self,
        identity: AgentIdentity,
        sandbox_id: UUID,
        generation: int,
        snapshot: ProfileSnapshot,
        artifact_id: UUID,
        deadline: datetime,
    ) -> SandboxView:
        record = self._controlled(identity, sandbox_id, generation)
        self.profiles.validate_snapshot(snapshot)
        if (
            record.snapshot.domain() != snapshot
            or record.spec.artifact_id != artifact_id
            or record.absolute_deadline != deadline
        ):
            raise BoxieError(409, "ensure differs from admitted sandbox")
        # PR1 ensure only retries an already-admitted intent; it never admits a new ID.
        if record.state == SandboxState.ACCEPTED and record.id not in self._workers:
            self._workers[record.id] = asyncio.create_task(self._provision(record))
        return record.view()

    async def _provision(self, record: Record) -> None:
        try:
            if self.monotonic() >= self._deadlines[record.id].absolute:
                await self._cleanup(record, TerminationReason.EXPIRED)
                return
            record.state = SandboxState.PROVISIONING
            record.creation_uncertain = True
            self._save()
            async with asyncio.timeout(self.config.docker_timeout):
                await self.docker.create(
                    record.name,
                    container_config(
                        record.snapshot.domain(),
                        self.artifacts.path(record.principal, record.spec.artifact_id),
                        record.labels,
                    ),
                )
                stream = await self.docker.attach(record.name)
                terminal = Terminal(self, record, stream)
                self.terminals[record.id] = terminal
                await self.docker.start(record.name)
            record.creation_uncertain = False
            record.state = SandboxState.READY
            record.idle_deadline = self.clock() + timedelta(seconds=record.spec.policy.idle_seconds)
            deadlines = self._deadlines[record.id]
            now = self.monotonic()
            deadlines.idle = now + record.spec.policy.idle_seconds
            deadlines.disconnect = now + record.spec.policy.disconnect_seconds
            self._save()
            terminal.start()
        except asyncio.CancelledError:
            # The remover joins this task before confirming daemon deletion.
            raise
        except Exception:
            logger.warning("sandbox provisioning failed: %s", record.id)
            await self._cleanup(record, TerminationReason.PROVISIONING_FAILED)

    async def terminate(
        self, principal: Principal, sandbox_id: UUID, reason: TerminationReason
    ) -> SandboxView:
        record = self.owned(principal, sandbox_id)
        return await self.remove(self.config.identity, sandbox_id, record.generation, reason)

    async def remove(
        self,
        identity: AgentIdentity,
        sandbox_id: UUID,
        generation: int,
        reason: TerminationReason,
        *,
        conditional: bool = False,
    ) -> SandboxView:
        record = self._controlled(identity, sandbox_id, generation)
        worker = self._workers.get(record.id)
        if worker and not worker.done() and worker is not asyncio.current_task():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        return await self._cleanup(record, reason, conditional=conditional)

    async def _cleanup(
        self, record: Record, reason: TerminationReason, *, conditional: bool = False
    ) -> SandboxView:
        lock = self._operations.setdefault(record.id, asyncio.Lock())
        async with lock:
            if record.state in FINAL:
                return record.view()
            if conditional:
                if record.state != SandboxState.READY:
                    return record.view()
                current_reason = self._expiry_reason(record, self.monotonic())
                if current_reason is None:
                    return record.view()
                reason = current_reason
            # No await separates revalidation and the state transition. Input and
            # attachment mutations below share this event-loop serialization point.
            record.state = SandboxState.TERMINATING
            record.cleanup_pending = True
            record.reason = record.reason or reason
            self._save()
            terminal = self.terminals.pop(record.id, None)
            if terminal is not None:
                try:
                    await terminal.close()
                except Exception:
                    logger.warning("terminal transport close failed: %s", record.id)
            try:
                async with asyncio.timeout(self.config.docker_timeout):
                    labels = await self.docker.labels(record.name)
                    if labels is not None and any(
                        labels.get(key) != value for key, value in record.labels.items()
                    ):
                        raise RuntimeError("container ownership mismatch")
                    if labels is None and record.creation_uncertain:
                        # A timed-out create may still be running inside Docker. Absence
                        # is not confirmation of deletion while its result is unknown.
                        return record.view()
                    if labels is not None:
                        await self.docker.delete(record.name)
                record.creation_uncertain = False
                record.cleanup_pending = False
                record.state = (
                    SandboxState.FAILED
                    if record.reason == TerminationReason.PROVISIONING_FAILED
                    else SandboxState.TERMINATED
                )
                self._save()
                self.artifacts.release(record.principal, record.spec.artifact_id, record.name)
                self._deadlines.pop(record.id, None)
            except Exception:
                logger.warning("sandbox cleanup remains pending: %s", record.id)
            return record.view()

    async def start(self) -> None:
        # Reconcile only persisted, precisely owned resources, never daemon-wide deletion.
        for record in self.records.values():
            if record.state not in FINAL:
                await self._cleanup(record, TerminationReason.AGENT_RESTART)
            else:
                self.artifacts.release(record.principal, record.spec.artifact_id, record.name)
        self._sweeper = asyncio.create_task(self._sweep_loop())

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.sweep_seconds)
            now = self.monotonic()
            for record in list(self.records.values()):
                if record.state in FINAL:
                    continue
                reason = None
                if record.cleanup_pending:
                    reason = record.reason or TerminationReason.REQUESTED
                else:
                    reason = self._expiry_reason(record, now)
                if reason:
                    pending = self._cleanup_tasks.get(record.id)
                    if pending is None or pending.done():
                        if pending is not None:
                            pending.result()
                        self._cleanup_tasks[record.id] = asyncio.create_task(
                            self.remove(
                                self.config.identity,
                                record.id,
                                record.generation,
                                reason,
                                conditional=not record.cleanup_pending
                                and reason
                                in {TerminationReason.IDLE, TerminationReason.DISCONNECTED},
                            )
                        )

    def _expiry_reason(self, record: Record, now: float) -> TerminationReason | None:
        deadlines = self._deadlines[record.id]
        if now >= deadlines.absolute:
            return TerminationReason.EXPIRED
        if deadlines.disconnect is not None and now >= deadlines.disconnect:
            return TerminationReason.DISCONNECTED
        if deadlines.idle is not None and now >= deadlines.idle:
            return TerminationReason.IDLE
        return None

    def validate_attachment(self, record: Record) -> None:
        if (
            record.state != SandboxState.READY
            or record.disconnected_at is not None
            or self._expiry_reason(record, self.monotonic()) is not None
        ):
            raise BoxieError(409, "terminal is unavailable")

    def attach(self, record: Record) -> None:
        # Acceptance may yield across expiry. Revalidate and clear without yielding.
        self.validate_attachment(record)
        self._deadlines[record.id].disconnect = None

    def accepted_input(self, record: Record) -> None:
        if record.state != SandboxState.READY:
            return
        now = self.monotonic()
        deadlines = self._deadlines[record.id]
        if now >= deadlines.absolute:
            return
        deadlines.idle = now + record.spec.policy.idle_seconds
        record.idle_deadline = self.clock() + timedelta(seconds=record.spec.policy.idle_seconds)
        self._save()

    def disconnected(self, record: Record) -> None:
        if record.state == SandboxState.READY and record.disconnected_at is None:
            record.disconnected_at = self.clock()
            self._deadlines[record.id].disconnect = (
                self.monotonic() + record.spec.policy.disconnect_seconds
            )
            self._save()

    async def close(self) -> None:
        self._stopping = True
        if self._sweeper:
            self._sweeper.cancel()
            await asyncio.gather(self._sweeper, return_exceptions=True)
        await asyncio.gather(
            *(
                self.remove(
                    self.config.identity,
                    record.id,
                    record.generation,
                    TerminationReason.AGENT_SHUTDOWN,
                )
                for record in self.records.values()
                if record.state not in FINAL
            )
        )
        await asyncio.gather(*self._cleanup_tasks.values())
        await self.docker.close()
