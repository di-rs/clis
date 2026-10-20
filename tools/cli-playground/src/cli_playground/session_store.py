"""SQLModel adapter for bindings and replayable remote-operation intents."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, delete, select, update

from cli_playground.database import connection
from cli_playground.domain import CreateIntent, SessionBinding, UploadIntent, check_build
from cli_playground.models import BuildIdentity
from cli_playground.sandbox import SandboxView, validate_sandbox
from cli_playground.storage import artifact_value, locked_preview, preview_value
from cli_playground.tables import Binding, Creation, Retirement, Upload


class SessionStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, binding: SessionBinding) -> None:
        with connection(self.session, write=True) as session:
            session.merge(Binding(**(binding.model_dump(mode="json") | {"id": binding.id})))

    def change(
        self, identifier: UUID, change: Callable[[SessionBinding], None], *, claimed: bool = False
    ) -> SessionBinding | None:
        with connection(self.session, write=True) as session:
            row = (
                session.exec(select(Binding).where(Binding.id == identifier).with_for_update())
            ).first()
            if row is None:
                return None
            binding = SessionBinding.model_validate(row.model_dump())
            change(binding)
            for name, value in binding.model_dump(mode="json", exclude={"id"}).items():
                setattr(row, name, value)
            if claimed:
                session.exec(delete(Creation).where(col(Creation.id) == identifier))
            return binding

    def bindings(self) -> list[SessionBinding]:
        with connection(self.session) as session:
            return [
                SessionBinding.model_validate(row.model_dump())
                for row in session.exec(select(Binding))
            ]

    def get(self, identifier: UUID) -> SessionBinding | None:
        with connection(self.session) as session:
            row = session.get(Binding, identifier)
            return SessionBinding.model_validate(row.model_dump()) if row else None

    def create_intent(self, intent: CreateIntent) -> None:
        with connection(self.session, write=True) as session:
            session.merge(Creation(**(intent.model_dump(mode="json") | {"id": intent.id})))

    def creates(self) -> list[CreateIntent]:
        with connection(self.session) as session:
            return [
                CreateIntent.model_validate(row.model_dump())
                for row in session.exec(select(Creation))
            ]

    def claimed(self, identifier: UUID) -> None:
        with connection(self.session, write=True) as session:
            session.exec(delete(Creation).where(col(Creation.id) == identifier))

    def upload_intent(self, intent: UploadIntent) -> None:
        with connection(self.session, write=True) as session:
            row = locked_preview(session, intent.artifact.pr)
            check_build(preview_value(session, row), row.closed, intent.build)
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

    def uploads(self) -> list[UploadIntent]:
        with connection(self.session) as session:
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
                for row in session.exec(select(Upload))
            ]

    def retire(self, identifier: UUID) -> None:
        with connection(self.session, write=True) as session:
            session.execute(
                insert(Retirement)
                .values(artifact_id=identifier, completed=False)
                .on_conflict_do_nothing()
            )

    def retirements(self) -> list[UUID]:
        with connection(self.session) as session:
            return list(
                session.exec(
                    select(Retirement.artifact_id).where(col(Retirement.completed).is_(False))
                )
            )

    def retired(self, identifier: UUID) -> None:
        with connection(self.session, write=True) as session:
            session.exec(
                update(Retirement)
                .where(col(Retirement.artifact_id) == identifier)
                .values(completed=True)
            )

    def stop_environments(self, identifiers: set[str]) -> None:
        with connection(self.session, write=True) as session:
            rows = session.exec(
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

    def recover_creation(self, intent: CreateIntent, sandbox: SandboxView) -> None:
        with connection(self.session, write=True) as session:
            row = (
                session.exec(select(Binding).where(Binding.id == intent.id).with_for_update())
            ).first()
            pending = session.get(Creation, intent.id, with_for_update=True)
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
            session.merge(Binding(**(binding.model_dump(mode="json") | {"id": binding.id})))
