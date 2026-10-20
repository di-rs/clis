"""Real PostgreSQL persistence and transaction boundaries."""

import asyncio
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from cli_playground.models import ArtifactMetadata

pytestmark = pytest.mark.integration


def test_publication_survives_new_engine_without_metadata_files(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from cli_playground.storage import EnvironmentStore

    from .helpers import database, migrate

    subprocess.run(
        ["alembic", "-c", str(Path(__file__).parents[1] / "alembic.ini"), "upgrade", "head"],
        env={**os.environ, "CLI_PLAYGROUND_DATABASE_URL": postgres_dsn},
        check=True,
        capture_output=True,
    )
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
    from cli_playground.domain import ArtifactConflictError, UploadIntent
    from cli_playground.session_store import SessionStore
    from cli_playground.storage import EnvironmentStore

    from .helpers import database, migrate

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
            assert await store.get(EnvironmentStore.identifier(123)) is None
            assert await operations.uploads() == []
            assert await operations.retirements() == [artifact.artifact_id]

    asyncio.run(exercise())


def test_failed_transaction_keeps_pending_upload_and_previous_revision(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli_playground.domain import UploadIntent
    from cli_playground.session_store import SessionStore
    from cli_playground.storage import EnvironmentStore

    from .helpers import database, migrate

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
            publish = EnvironmentStore._publish

            async def failed(*args: object) -> None:
                await publish(*args)  # type: ignore[arg-type]
                raise OSError("commit interrupted")

            with monkeypatch.context() as fault:
                fault.setattr(EnvironmentStore, "_publish", failed)
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
    from cli_playground.tables import Table
    from sqlalchemy import create_engine

    from .helpers import migrate

    migrate(postgres_dsn)
    engine = create_engine(postgres_dsn)
    try:
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Table.metadata) == []
    finally:
        engine.dispose()


def test_correlation_upgrade_preserves_pending_legacy_retirements(postgres_dsn: str) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_dsn.replace("%", "%%"))
    command.upgrade(config, "0001_metadata")
    artifact = uuid4()
    engine = create_engine(postgres_dsn)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO artifact_retirement (artifact_id, completed) VALUES (:id, false)"
                ),
                {"id": artifact},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT artifact_id, completed, request_id FROM artifact_retirement")
            ).one() == (artifact, False, None)
        command.downgrade(config, "0001_metadata")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT artifact_id, completed FROM artifact_retirement")
            ).one() == (artifact, False)
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_cleanup_skips_tombstones_and_collects_without_locking_previews(postgres_dsn: str) -> None:
    from cli_playground.domain import UploadIntent
    from cli_playground.session_store import SessionStore
    from cli_playground.storage import EnvironmentStore
    from cli_playground.tables import Preview
    from sqlmodel import select

    from .helpers import database, migrate

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
                await store.publish(artifact=artifact, build=None)
            for pr in (1, 2, 3):
                await store.close_pull_request(pr=pr)
            await store.reopen_pull_request(2)
            await operations.upload_intent(
                UploadIntent(artifact=artifacts[1], build=None, previous_revision=None)
            )
            await store.close_pull_request(2)
            await operations.retire(artifacts[2].artifact_id)
            await operations.retired(artifacts[2].artifact_id)
            inactive = await store.inactive_identifiers()
            assert inactive == {EnvironmentStore.identifier(pr) for pr in (1, 2, 3)}
            assert await store.remove(inactive) == 3
            assert await store.inactive_identifiers() == set()
            assert await store.is_closed(1)
            # Collection must not wait for an unrelated preview mutation.
            async with sessions.begin() as session:
                await session.exec(select(Preview).where(Preview.pr == 4).with_for_update())
                await asyncio.wait_for(store.collect_artifacts(), timeout=1)
            assert await operations.retirements() == [artifacts[0].artifact_id]
            await store.collect_artifacts()
            assert await operations.retirements() == [artifacts[0].artifact_id]

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_database_io_yields_to_event_loop_and_cancellation_rolls_back(
    postgres_dsn: str, cancel: bool
) -> None:
    from cli_playground.database import connection
    from cli_playground.tables import Preview
    from sqlalchemy import text

    from .helpers import database, migrate

    migrate(postgres_dsn)

    async def exercise() -> None:
        async with database(postgres_dsn) as sessions:
            entered = asyncio.Event()

            async def operation() -> int:
                async with connection(sessions, write=True) as session:
                    session.add(Preview(pr=1, id="preview"))
                    await session.flush()
                    entered.set()
                    await session.scalar(text("SELECT pg_sleep(0.2)"))
                return 42

            pending = asyncio.create_task(operation())
            await entered.wait()
            await asyncio.sleep(0.02)
            assert not pending.done(), "database I/O blocked the event loop"
            if cancel:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                assert await pending == 42
            async with connection(sessions) as session:
                assert (await session.get(Preview, 1) is None) == cancel

    asyncio.run(exercise())
