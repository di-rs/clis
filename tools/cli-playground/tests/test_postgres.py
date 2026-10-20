"""Real PostgreSQL persistence and transaction boundaries."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from cli_playground.models import ArtifactMetadata

pytestmark = pytest.mark.integration


def test_publication_survives_new_engine_without_metadata_files(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from cli_playground.database import database
    from cli_playground.migrate import migrate
    from cli_playground.storage import EnvironmentStore

    migrate(postgres_dsn)
    migrate(postgres_dsn)
    artifact = ArtifactMetadata(
        pr=123,
        sha="a" * 40,
        revision="a" * 40,
        artifact_id=uuid4(),
        sha256="b" * 64,
        clis=["echor"],
        expires_at=100,
    )

    async def exercise() -> None:
        async with database(postgres_dsn) as sessions:
            store = EnvironmentStore(sessions, preview_seconds=100, clock=lambda: 1)
            saved = await store.publish(artifact, None)
        async with database(postgres_dsn) as sessions:
            store = EnvironmentStore(sessions, preview_seconds=100, clock=lambda: 1)
            assert await store.get(saved.id) == saved
            await store.close_pull_request(123)
            assert await store.get(saved.id) is None
            await store.reopen_pull_request(123)
            assert await store.get(saved.id) == saved
        assert not list(tmp_path.rglob("*.json"))

    asyncio.run(exercise())


def test_cancelled_upload_stays_cancelled_after_reopen(postgres_dsn: str) -> None:
    from cli_playground.database import database
    from cli_playground.domain import ArtifactConflictError, UploadIntent
    from cli_playground.migrate import migrate
    from cli_playground.session_store import SessionStore
    from cli_playground.storage import EnvironmentStore

    migrate(postgres_dsn)
    artifact = ArtifactMetadata(
        pr=123,
        sha="a" * 40,
        revision="a" * 40,
        artifact_id=uuid4(),
        sha256="b" * 64,
        clis=["echor"],
        expires_at=100,
    )

    async def exercise() -> None:
        async with database(postgres_dsn) as sessions:
            store = EnvironmentStore(sessions, preview_seconds=100, clock=lambda: 1)
            operations = SessionStore(sessions)
            await operations.upload_intent(
                UploadIntent(artifact=artifact, build=None, previous_revision=None)
            )
            await store.close_pull_request(123)
            await store.reopen_pull_request(123)
            with pytest.raises(ArtifactConflictError, match="cancelled"):
                await store.finish_upload(artifact, None)
            assert await store.get(store.identifier(123)) is None
            assert await operations.uploads() == []
            assert await operations.retirements() == [artifact.artifact_id]

    asyncio.run(exercise())


def test_failed_transaction_keeps_pending_upload_and_previous_revision(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli_playground.database import database
    from cli_playground.domain import UploadIntent
    from cli_playground.migrate import migrate
    from cli_playground.session_store import SessionStore
    from cli_playground.storage import EnvironmentStore

    migrate(postgres_dsn)
    first = ArtifactMetadata(
        pr=123,
        sha="a" * 40,
        revision="a" * 40,
        artifact_id=uuid4(),
        sha256="b" * 64,
        clis=["echor"],
        expires_at=100,
    )
    second = first.model_copy(
        update={"sha": "c" * 40, "revision": "c" * 40, "artifact_id": uuid4()}
    )

    async def exercise() -> None:
        async with database(postgres_dsn) as sessions:
            store = EnvironmentStore(sessions, preview_seconds=100, clock=lambda: 1)
            operations = SessionStore(sessions)
            before = await store.publish(first, None)
            await operations.upload_intent(
                UploadIntent(artifact=second, build=None, previous_revision=first.revision)
            )
            publish = store._publish

            async def failed(*args: object) -> None:
                await publish(*args)  # type: ignore[arg-type]
                raise OSError("commit interrupted")

            with monkeypatch.context() as fault:
                fault.setattr(store, "_publish", failed)
                with pytest.raises(OSError, match="interrupted"):
                    await store.finish_upload(second, None)
            assert await store.get(before.id) == before
            assert await store.revision(123, second.revision) is None
            assert len(await operations.uploads()) == 1
            after = await store.finish_upload(second, None)
            assert after.artifact_id == second.artifact_id
            assert await operations.uploads() == []
            # Replaying after a lost commit acknowledgement preserves the committed UUID.
            assert await store.finish_upload(second, None) == after

    asyncio.run(exercise())


def test_alembic_matches_only_playground_tables(postgres_dsn: str) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from cli_playground.database import database_url
    from cli_playground.migrate import migrate
    from cli_playground.tables import Table
    from sqlalchemy import create_engine

    migrate(postgres_dsn)
    engine = create_engine(database_url(postgres_dsn))
    try:
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Table.metadata) == []
    finally:
        engine.dispose()


def test_cleanup_skips_tombstones_and_collects_without_locking_previews(postgres_dsn: str) -> None:
    from cli_playground.database import connection, database
    from cli_playground.domain import UploadIntent
    from cli_playground.migrate import migrate
    from cli_playground.session_store import SessionStore
    from cli_playground.storage import EnvironmentStore
    from cli_playground.tables import Preview
    from sqlmodel import select

    migrate(postgres_dsn)

    async def exercise() -> None:
        async with database(postgres_dsn) as sessions:
            store = EnvironmentStore(sessions, preview_seconds=100, clock=lambda: 1)
            operations = SessionStore(sessions)
            artifacts = [
                ArtifactMetadata(
                    pr=pr,
                    sha="a" * 40,
                    revision="a" * 40,
                    artifact_id=uuid4(),
                    sha256="b" * 64,
                    clis=["echor"],
                    expires_at=100,
                )
                for pr in range(1, 5)
            ]
            for artifact in artifacts:
                await store.publish(artifact, None)
            for pr in (1, 2, 3):
                await store.close_pull_request(pr)
            await store.reopen_pull_request(2)
            await operations.upload_intent(
                UploadIntent(artifact=artifacts[1], build=None, previous_revision=None)
            )
            await store.close_pull_request(2)
            await operations.retire(artifacts[2].artifact_id)
            await operations.retired(artifacts[2].artifact_id)
            inactive = await store.inactive_identifiers()
            assert inactive == {store.identifier(pr) for pr in (1, 2, 3)}
            assert await store.remove(inactive) == 3
            assert await store.inactive_identifiers() == set()
            assert await store.is_closed(1)
            # Collection must not wait for an unrelated preview mutation.
            async with connection(sessions, write=True) as session:
                await session.exec(select(Preview).where(Preview.pr == 4).with_for_update())
                await asyncio.wait_for(store.collect_artifacts(), timeout=1)
            assert await operations.retirements() == [artifacts[0].artifact_id]
            await store.collect_artifacts()
            assert await operations.retirements() == [artifacts[0].artifact_id]

    asyncio.run(exercise())
