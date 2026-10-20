"""SQLModel adapter for previews and immutable revisions."""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import false
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select, update

from cli_playground.database import connection
from cli_playground.domain import (
    ArtifactConflictError,
    UploadIntent,
    check_build,
    check_upload,
    identifier,
    next_build,
    publication,
)
from cli_playground.models import (
    ArtifactMetadata,
    BuildIdentity,
    BuildStatus,
    BuildUpdate,
    Environment,
    EnvironmentMetadata,
    PreviewRecord,
)
from cli_playground.tables import Binding, Preview, Retirement, Revision, Upload


def locked_preview(session: Session, pr: int) -> Preview:
    session.execute(
        insert(Preview).values(pr=pr, id=identifier(pr), closed=False).on_conflict_do_nothing()
    )
    row = (session.exec(select(Preview).where(Preview.pr == pr).with_for_update())).one()
    return row


def artifact_value(row: Revision | Upload) -> ArtifactMetadata:
    return ArtifactMetadata.model_validate(
        {name: getattr(row, name) for name in ArtifactMetadata.model_fields}
    )


def preview_value(session: Session, row: Preview) -> PreviewRecord | None:
    if row.expires_at is None:
        return None
    revision = (
        session.get(Revision, (row.pr, row.current_revision)) if row.current_revision else None
    )
    build = (
        BuildStatus.model_validate(
            dict(
                sha=row.build_sha,
                run_id=row.run_id,
                run_number=row.run_number,
                run_attempt=row.run_attempt,
                state=row.build_state,
            )
        )
        if row.build_state
        else None
    )
    return PreviewRecord(
        pr=row.pr,
        expires_at=row.expires_at,
        build=build,
        artifact=artifact_value(revision) if revision else None,
    )


def assign_record(row: Preview, record: PreviewRecord) -> None:
    row.expires_at = record.expires_at
    row.current_revision = record.artifact.revision if record.artifact else None
    row.build_sha = record.build.sha if record.build else None
    row.run_id = record.build.run_id if record.build else None
    row.run_number = record.build.run_number if record.build else None
    row.run_attempt = record.build.run_attempt if record.build else None
    row.build_state = record.build.state if record.build else None


class EnvironmentStore:
    @staticmethod
    def identifier(pr: int) -> str:
        return identifier(pr)

    def __init__(
        self,
        session: Session,
        *,
        preview_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.session, self.preview_seconds, self.clock = session, preview_seconds, clock

    def is_closed(self, pr: int) -> bool:
        with connection(self.session) as session:
            row = session.get(Preview, pr)
            return row is not None and row.closed

    def close_pull_request(self, pr: int) -> None:
        with connection(self.session, write=True) as session:
            row = locked_preview(session, pr)
            row.closed = True
            session.exec(update(Upload).where(col(Upload.pr) == pr).values(cancelled=True))

    def reopen_pull_request(self, pr: int) -> None:
        with connection(self.session, write=True) as session:
            row = locked_preview(session, pr)
            if row.closed:
                session.exec(update(Upload).where(col(Upload.pr) == pr).values(cancelled=True))
            row.closed = False

    def record(self, identifier: str, *, include_inactive: bool = False) -> PreviewRecord | None:
        with connection(self.session) as session:
            row = (session.exec(select(Preview).where(Preview.id == identifier))).first()
            if row is None or (
                not include_inactive
                and (row.closed or row.expires_at is None or row.expires_at <= self.clock())
            ):
                return None
            return preview_value(session, row)

    def get(self, identifier: str, *, include_inactive: bool = False) -> Environment | None:
        record = self.record(identifier, include_inactive=include_inactive)
        if (
            record is None
            or record.artifact is None
            or (not include_inactive and record.artifact.expires_at <= self.clock())
        ):
            return None
        return Environment(id=identifier, **record.artifact.model_dump())

    def metadata(self, identifier: str) -> EnvironmentMetadata | None:
        record = self.record(identifier)
        if record is None:
            return None
        artifact = (
            record.artifact
            if record.artifact and record.artifact.expires_at > self.clock()
            else None
        )
        return EnvironmentMetadata(
            id=identifier,
            pr=record.pr,
            sha=artifact.sha if artifact else None,
            revision=artifact.revision if artifact else None,
            clis=artifact.clis if artifact else [],
            expires_at=datetime.fromtimestamp(
                artifact.expires_at if artifact else record.expires_at, UTC
            ).isoformat(),
            build=record.build,
        )

    def inactive_identifiers(self) -> set[str]:
        with connection(self.session) as session:
            needs_stop = select(Binding.preview_id).where(
                col(Binding.pending_stop)
                | col(Binding.sandbox)["cleanup_pending"].as_boolean()
                | col(Binding.sandbox)["state"].as_string().not_in(["terminated", "failed"])
            )
            return set(
                session.exec(
                    select(Preview.id).where(
                        (
                            (col(Preview.expires_at) <= self.clock())
                            | col(Preview.closed)
                            | col(Preview.expires_at).is_(None)
                        )
                        & (col(Preview.expires_at).is_not(None) | col(Preview.id).in_(needs_stop))
                    )
                )
            )

    def remove(self, identifiers: set[str]) -> int:
        count = 0
        with connection(self.session, write=True) as session:
            rows = session.exec(
                select(Preview)
                .where(col(Preview.id).in_(identifiers))
                .order_by(col(Preview.pr))
                .with_for_update()
            )
            for row in rows:
                if row.expires_at is not None and (row.closed or row.expires_at <= self.clock()):
                    row.expires_at = None
                    row.current_revision = None
                    row.build_sha = row.run_id = row.run_number = row.run_attempt = (
                        row.build_state
                    ) = None
                    count += 1
        return count

    def for_pull_request(self, pr: int) -> set[str]:
        return (
            {identifier(pr)}
            if self.record(identifier(pr), include_inactive=True) is not None
            else set()
        )

    def check_build(self, pr: int, build: BuildIdentity | None) -> PreviewRecord | None:
        with connection(self.session, write=True) as session:
            row = locked_preview(session, pr)
            record = preview_value(session, row)
            check_build(record, row.closed, build)
            return record

    def update_build(self, pr: int, update: BuildUpdate) -> EnvironmentMetadata:
        with connection(self.session, write=True) as session:
            row = locked_preview(session, pr)
            record = preview_value(session, row)
            check_build(record, row.closed, update)
            assign_record(row, next_build(record, pr, update, self.clock() + self.preview_seconds))
        result = self.metadata(identifier(pr))
        if result is None:
            raise ArtifactConflictError("preview is no longer active")
        return result

    def revisions(self) -> list[ArtifactMetadata]:
        with connection(self.session) as session:
            return [artifact_value(row) for row in session.exec(select(Revision))]

    def revision(self, pr: int, revision: str) -> ArtifactMetadata | None:
        with connection(self.session) as session:
            row = session.get(Revision, (pr, revision))
            return artifact_value(row) if row else None

    def publish(self, artifact: ArtifactMetadata, build: BuildIdentity | None) -> Environment:
        with connection(self.session, write=True) as session:
            row = locked_preview(session, artifact.pr)
            return self._publish(session, row, artifact, build)

    def _publish(
        self,
        session: Session,
        row: Preview,
        artifact: ArtifactMetadata,
        build: BuildIdentity | None,
    ) -> Environment:
        record = preview_value(session, row)
        previous = session.get(Revision, (artifact.pr, artifact.revision))
        result = publication(
            record,
            row.closed,
            artifact_value(previous) if previous else None,
            artifact,
            build,
            self.clock(),
        )
        assert result.artifact is not None
        session.merge(Revision(**result.artifact.model_dump()))
        session.flush()
        assign_record(row, result)
        return Environment(id=row.id, **result.artifact.model_dump())

    def finish_upload(self, artifact: ArtifactMetadata, build: BuildIdentity | None) -> Environment:
        conflict: ArtifactConflictError | None = None
        result: Environment | None = None
        with connection(self.session, write=True) as session:
            row = locked_preview(session, artifact.pr)
            intent = session.get(Upload, artifact.artifact_id)
            if intent is None:
                previous = session.get(Revision, (artifact.pr, artifact.revision))
                if previous and previous.artifact_id == artifact.artifact_id:
                    return Environment(id=row.id, **artifact_value(previous).model_dump())
                raise ArtifactConflictError("upload is no longer pending")
            try:
                current = preview_value(session, row)
                check_upload(
                    UploadIntent(
                        artifact=artifact,
                        build=build,
                        previous_revision=intent.previous_revision,
                        cancelled=intent.cancelled,
                    ),
                    current,
                    row.closed,
                    self.clock(),
                )
                result = self._publish(session, row, artifact, build)
                if result.artifact_id != artifact.artifact_id:
                    session.execute(
                        insert(Retirement)
                        .values(artifact_id=artifact.artifact_id, completed=False)
                        .on_conflict_do_nothing()
                    )
            except ArtifactConflictError as error:
                conflict = error
                session.execute(
                    insert(Retirement)
                    .values(artifact_id=artifact.artifact_id, completed=False)
                    .on_conflict_do_nothing()
                )
            session.delete(intent)
        if conflict is not None:
            raise conflict
        assert result is not None
        return result

    def collect_artifacts(self) -> None:
        with connection(self.session, write=True) as session:
            now = self.clock()
            active = (
                select(Revision.artifact_id)
                .join(
                    Preview,
                    (col(Preview.pr) == col(Revision.pr))
                    & (col(Preview.current_revision) == col(Revision.revision)),
                )
                .where(
                    ~col(Preview.closed),
                    col(Preview.expires_at) > now,
                    col(Revision.expires_at) > now,
                )
            )
            # One snapshot sees a completing upload as either pending or current.
            stale = select(col(Revision.artifact_id), false()).where(
                col(Revision.artifact_id).not_in(select(Upload.artifact_id)),
                col(Revision.artifact_id).not_in(active),
                col(Revision.artifact_id).not_in(select(Retirement.artifact_id)),
            )
            session.execute(
                insert(Retirement)
                .from_select(["artifact_id", "completed"], stale)
                .on_conflict_do_nothing()
            )
