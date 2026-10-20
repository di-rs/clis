"""SQLModel adapter for bindings and replayable remote-operation intents."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, delete, select, update

from cli_playground.database import SessionFactory, connection
from cli_playground.domain import CreateIntent, SessionBinding, UploadIntent, check_build
from cli_playground.models import BuildIdentity
from cli_playground.sandbox import SandboxView, validate_sandbox
from cli_playground.storage import artifact_value, locked_preview, preview_value
from cli_playground.tables import Binding, Creation, Retirement, Upload


class SessionStore:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    async def save(self, binding: SessionBinding) -> None:
        async with connection(self.sessions, write=True) as session:
            await session.merge(Binding(**(binding.model_dump(mode="json") | {"id": binding.id})))

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
            for name, value in binding.model_dump(mode="json", exclude={"id"}).items():
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
            await session.merge(Creation(**(intent.model_dump(mode="json") | {"id": intent.id})))

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

    async def retire(self, identifier: UUID) -> None:
        async with connection(self.sessions, write=True) as session:
            await session.exec(
                insert(Retirement)
                .values(artifact_id=identifier, completed=False)
                .on_conflict_do_nothing()
            )

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
            )
            await session.merge(Binding(**(binding.model_dump(mode="json") | {"id": binding.id})))
