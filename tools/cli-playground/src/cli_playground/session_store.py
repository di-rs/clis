"""SQLModel adapter for bindings and replayable remote-operation intents."""

from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, delete, select, update

from cli_playground.database import SessionFactory, connection
from cli_playground.domain import CreateIntent, SessionBinding, UploadIntent, check_build
from cli_playground.models import BuildIdentity
from cli_playground.observability import current_request_id
from cli_playground.sandbox import BoxieUnavailable, SandboxView, validate_sandbox
from cli_playground.storage import artifact_value, locked_preview, preview_value
from cli_playground.tables import Binding, Creation, Retirement, Upload


class SessionStore:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    async def save(self, binding: SessionBinding) -> None:
        async with connection(self.sessions, write=True) as session:
            await session.merge(Binding.model_validate(binding.model_dump(mode="json")))

    async def change(
        self, identifier: UUID, change: Callable[[SessionBinding], None], *, claimed: bool = False
    ) -> SessionBinding | None:
        async with connection(self.sessions, write=True) as session:
            row = (
                await session.exec(
                    select(Binding).where(Binding.id == identifier).with_for_update()
                )
            ).first()
            if row is None:
                return None
            binding = SessionBinding.model_validate(row.model_dump())
            change(binding)
            updated = Binding.model_validate(binding.model_dump(mode="json"))
            for name, value in updated.model_dump(exclude={"id"}).items():
                setattr(row, name, value)
            if claimed:
                await session.exec(delete(Creation).where(col(Creation.id) == identifier))
            return binding

    async def bindings(self) -> list[SessionBinding]:
        async with connection(self.sessions) as session:
            return [
                SessionBinding.model_validate(row.model_dump())
                for row in await session.exec(select(Binding))
            ]

    async def pending_stops(self) -> list[SessionBinding]:
        async with connection(self.sessions) as session:
            return [
                SessionBinding.model_validate(row.model_dump())
                for row in await session.exec(
                    select(Binding).where(col(Binding.pending_stop).is_(True))
                )
            ]

    async def pending_attachments(self, *, before: float | None = None) -> list[SessionBinding]:
        query = select(Binding).where(col(Binding.attachment_deadline).is_not(None))
        if before is not None:
            query = query.where(col(Binding.attachment_deadline) <= before)
        async with connection(self.sessions) as session:
            return [
                SessionBinding.model_validate(row.model_dump()) for row in await session.exec(query)
            ]

    async def get(self, identifier: UUID) -> SessionBinding | None:
        async with connection(self.sessions) as session:
            row = await session.get(Binding, identifier)
            return SessionBinding.model_validate(row.model_dump()) if row else None

    async def create_intent(self, intent: CreateIntent) -> None:
        async with connection(self.sessions, write=True) as session:
            # Serialize admission accounting across workers while allowing updates to
            # existing ambiguous intents even when the queue is full.
            await session.scalar(text("SELECT pg_advisory_xact_lock(736216, 1)"))
            if await session.get(Creation, intent.id) is None:
                count = await session.scalar(select(func.count()).select_from(Creation))
                if count is not None and count >= 32:
                    raise BoxieUnavailable("sandbox admission recovery is full")
            await session.merge(Creation.model_validate(intent.model_dump(mode="json")))

    async def creates(self) -> list[CreateIntent]:
        async with connection(self.sessions) as session:
            return [
                CreateIntent.model_validate(row.model_dump())
                for row in await session.exec(select(Creation))
            ]

    async def claimed(self, identifier: UUID) -> None:
        async with connection(self.sessions, write=True) as session:
            await session.exec(delete(Creation).where(col(Creation.id) == identifier))

    async def upload_intent(self, intent: UploadIntent) -> None:
        async with connection(self.sessions, write=True) as session:
            row = await locked_preview(session, intent.artifact.pr)
            check_build(await preview_value(session, row), row.closed, intent.build)
            session.add(
                Upload(
                    request_id=intent.request_id,
                    **intent.artifact.model_dump(),
                    build_sha=intent.build.sha if intent.build else None,
                    run_id=intent.build.run_id if intent.build else None,
                    run_number=intent.build.run_number if intent.build else None,
                    run_attempt=intent.build.run_attempt if intent.build else None,
                    previous_revision=intent.previous_revision,
                    cancelled=intent.cancelled,
                )
            )

    async def uploads(self) -> list[UploadIntent]:
        async with connection(self.sessions) as session:
            return [
                UploadIntent(
                    request_id=row.request_id,
                    artifact=artifact_value(row),
                    build=BuildIdentity.model_validate(
                        dict(
                            sha=row.build_sha,
                            run_id=row.run_id,
                            run_number=row.run_number,
                            run_attempt=row.run_attempt,
                        )
                    )
                    if row.build_sha
                    else None,
                    previous_revision=row.previous_revision,
                    cancelled=row.cancelled,
                )
                for row in await session.exec(select(Upload))
            ]

    async def retire(self, identifier: UUID, *, request_id: UUID | None = None) -> None:
        async with connection(self.sessions, write=True) as session:
            await session.exec(
                insert(Retirement)
                .values(
                    artifact_id=identifier,
                    completed=False,
                    request_id=request_id or UUID(current_request_id() or str(uuid4())),
                )
                .on_conflict_do_nothing()
            )

    async def retirement_request_id(self, identifier: UUID) -> UUID | None:
        async with connection(self.sessions) as session:
            row = await session.get(Retirement, identifier)
            return row.request_id if row else None

    async def retirements(self) -> list[UUID]:
        async with connection(self.sessions) as session:
            return list(
                await session.exec(
                    select(Retirement.artifact_id).where(col(Retirement.completed).is_(False))
                )
            )

    async def retired(self, identifier: UUID) -> None:
        async with connection(self.sessions, write=True) as session:
            await session.exec(
                update(Retirement)
                .where(col(Retirement.artifact_id) == identifier)
                .values(completed=True)
            )

    async def stop_environments(self, identifiers: set[str]) -> None:
        async with connection(self.sessions, write=True) as session:
            rows = await session.exec(
                select(Binding)
                .where(col(Binding.preview_id).in_(identifiers))
                .order_by(col(Binding.id))
                .with_for_update()
            )
            for row in rows:
                binding = SessionBinding.model_validate(row.model_dump())
                if binding.sandbox.cleanup_pending or binding.sandbox.state not in {
                    "terminated",
                    "failed",
                }:
                    if not row.pending_stop:
                        row.stop_request_id = UUID(current_request_id() or str(uuid4()))
                    row.pending_stop = True

    async def recover_creation(self, intent: CreateIntent, sandbox: SandboxView) -> None:
        async with connection(self.sessions, write=True) as session:
            row = (
                await session.exec(select(Binding).where(Binding.id == intent.id).with_for_update())
            ).first()
            pending = await session.get(Creation, intent.id, with_for_update=True)
            if pending is None or (
                row is not None
                and (row.attached or row.attachment_deadline is not None or row.pending_stop)
            ):
                return
            if row is not None:
                validate_sandbox(sandbox, SessionBinding.model_validate(row.model_dump()).sandbox)
            binding = SessionBinding(
                id=intent.id,
                preview_id=intent.preview_id,
                revision=intent.revision,
                credential_hash=intent.credential_hash,
                sandbox=sandbox,
                pending_stop=True,
                stop_request_id=UUID(current_request_id() or str(uuid4())),
            )
            await session.merge(Binding.model_validate(binding.model_dump(mode="json")))
