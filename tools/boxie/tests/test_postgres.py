"""Exercise real transactional admission, crash boundaries and ownership accounting."""

import asyncio
import hashlib
import importlib.util
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from boxie.domain.errors import BoxieError
from boxie.domain.models import (
    OrchestratorIdentity,
    Principal,
    SandboxState,
    TerminationReason,
)
from boxie.persistence.database import database as session_database
from boxie.persistence.migrate import migrate
from boxie.persistence.models import (
    Artifact,
    Outbox,
    Sandbox,
    SandboxArtifactRef,
)
from boxie.persistence.postgres import PostgresLifecycle
from boxie_test_support.dependencies import postgres_dsn as postgres_dsn
from boxie_test_support.dependencies import postgres_server as postgres_server
from boxie_test_support.durable import launch_execution
from boxie_test_support.orchestrator import Clock, bundle
from boxie_test_support.terminal_network import network
from database_support import add_check
from database_support import counts as counts
from database_support import database as database
from database_support import upload as upload
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    create_engine,
    delete,
    func,
    inspect,
    update,
)
from sqlalchemy.exc import DBAPIError, IntegrityError, ResourceClosedError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).parents[1] / "src/boxie/persistence/migrations"
OWNER = Principal("one")


def test_nine_concurrent_creates_reserve_exactly_eight(postgres_dsn: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            spec = await upload(artifacts)
            result = await asyncio.gather(
                *(life.submit(OWNER, str(i), spec) for i in range(9)), return_exceptions=True
            )
            assert sum(not isinstance(item, Exception) for item in result) == 8
            errors = [item for item in result if isinstance(item, BoxieError)]
            assert len(errors) == 1 and errors[0].status == 429
            assert await counts(sessions) == (8, 8, 8, 8)

    asyncio.run(exercise())


def test_identical_retries_old_snapshot_and_conflicting_key(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        import json

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            spec = await upload(artifacts)
            views = await asyncio.gather(*(life.submit(OWNER, "same", spec) for _ in range(20)))
            assert len({item.id for item in views}) == 1
            assert await counts(sessions) == (1, 1, 1, 1)
            first = await life.load_request(views[0].id)
            data = json.loads(config.profiles_file.read_text())
            data["shell"]["image_digest"] = "example.org/shell@sha256:" + "4" * 64
            config.profiles_file.write_text(json.dumps(data))
            # A fresh trusted resolver reads the operator's new profile file.
            from boxie.sandbox.transport.client import OrchestratorClient

            async with network(tmp_path / "reloaded-resolver") as harness:
                harness.config.profiles_file.write_text(config.profiles_file.read_text())
                harness.orchestrator.profiles.reload()
                async with OrchestratorClient(
                    harness.orchestrator_url, b"orchestrator-test-secret"
                ) as resolver:
                    life.resolver = resolver
                    retry = await life.submit(OWNER, "same", spec)
                    new = await life.submit(OWNER, "new", spec)
                    assert retry == views[0]
                    assert (await life.load_request(retry.id)).snapshot == first.snapshot
                    assert (await life.load_request(new.id)).snapshot.image_digest.endswith(
                        "4" * 64
                    )
            await artifacts.retire(OWNER, spec.artifact_id)
            assert await life.submit(OWNER, "same", spec) == views[0]
            with pytest.raises(BoxieError) as conflict:
                await life.submit(OWNER, "same", replace(spec, env={"GREETING": "hello"}))
            assert conflict.value.status == 409
            with pytest.raises(BoxieError) as hidden:
                await life.get(Principal("other"), views[0].id)
            assert hidden.value.status == 404

    asyncio.run(exercise())


def test_stop_before_dispatch_is_durable_and_retains_accounting(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            view = await life.submit(OWNER, "stop", await upload(artifacts))
            stopped = await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            assert stopped.state == SandboxState.TERMINATING and stopped.cleanup_pending
            assert (await life.load_request(view.id)).desired_stop
            await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            assert await counts(sessions) == (1, 1, 1, 2)

    asyncio.run(exercise())


def test_retirement_races_admission_without_losing_files(postgres_dsn: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            spec = await upload(artifacts)
            outcomes = await asyncio.gather(
                life.submit(OWNER, "race", spec),
                artifacts.retire(OWNER, spec.artifact_id),
                return_exceptions=True,
            )
            view: Any = outcomes[0]
            if isinstance(view, BoxieError):
                assert view.status == 409
                assert await counts(sessions) == (0, 0, 0, 0)
            else:
                assert not isinstance(view, Exception)
                assert (await artifacts.path(OWNER, spec.artifact_id)).is_dir()
                assert await counts(sessions) == (1, 1, 1, 1)
            with pytest.raises(BoxieError):
                await life.submit(OWNER, "after", spec)

    asyncio.run(exercise())


def test_outbox_failure_rolls_back_all_admission_writes(postgres_dsn: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            spec = await upload(artifacts)
            await add_check(sessions, col(Outbox.kind) != "start", "injected_failure")
            with pytest.raises(IntegrityError) as rejected:
                await life.submit(OWNER, "rollback", spec)
            assert isinstance(rejected.value.orig, psycopg.errors.CheckViolation)
            assert rejected.value.orig.diag.constraint_name == "injected_failure"
            assert await counts(sessions) == (0, 0, 0, 0)

    asyncio.run(exercise())


def test_alembic_upgrades_are_idempotent_and_failed_revision_is_atomic(
    postgres_dsn: str, tmp_path: Path
) -> None:
    import shutil

    from boxie.persistence.database import database_url

    migrations = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, migrations)
    migrate(postgres_dsn, migrations)
    migrate(postgres_dsn, migrations)
    head = ScriptDirectory(str(migrations)).get_current_head()
    assert head is not None
    engine = create_engine(database_url(postgres_dsn))
    try:
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == head
            assert {
                "alembic_version",
                "artifacts",
                "sandboxes",
                "capacity_slots",
                "sandbox_artifact_refs",
                "outbox",
                "orchestrator_observations",
            } <= set(inspect(connection).get_table_names())
        (migrations / "versions" / "failed_revision.py").write_text(
            "from alembic import op\n"
            "import sqlalchemy as sa\n"
            "revision = 'failed_revision'\n"
            f"down_revision = {head!r}\n"
            "branch_labels = None\n"
            "depends_on = None\n"
            "def upgrade():\n"
            "    op.create_table('half_applied', sa.Column('id', sa.Integer(), primary_key=True))\n"
            "    raise RuntimeError('injected revision failure')\n"
            "def downgrade():\n"
            "    op.drop_table('half_applied')\n"
        )
        with pytest.raises(RuntimeError, match="injected revision failure"):
            migrate(postgres_dsn, migrations)
        with engine.connect() as connection:
            assert not inspect(connection).has_table("half_applied")
            assert MigrationContext.configure(connection).get_current_revision() == head
    finally:
        engine.dispose()


def test_only_fenced_confirmed_removal_releases_capacity_and_references(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import OrchestratorObservation

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "fence", await upload(artifacts))
            assert not view.cleanup_pending
            boot = uuid4()
            assert hasattr(life, "bind_orchestrator_boot"), "authenticated boot binding missing"
            assert await life.bind_orchestrator_boot(OWNER, config.identity, view.id, 1, None, boot)
            observation = OrchestratorObservation(
                view.id,
                OWNER,
                config.identity,
                1,
                boot,
                1,
                datetime.now(UTC),
                SandboxState.FAILED,
                False,
            )
            assert await life.record_observation(observation)
            assert await counts(sessions) == (1, 1, 1, 1)
            assert (await life.get(OWNER, view.id)).cleanup_pending
            confirmed = replace(
                observation, event_version=2, removal_confirmed=True, cleanup_pending=False
            )
            for bad in (
                replace(confirmed, boot_id=uuid4()),
                replace(confirmed, generation=2),
                replace(confirmed, principal=Principal("other")),
                replace(confirmed, identity=OrchestratorIdentity(uuid4(), config.identity.host_id)),
                replace(confirmed, event_version=1),
            ):
                assert not await life.confirm_removed(bad)
            assert await counts(sessions) == (1, 1, 1, 1)
            assert await life.confirm_removed(confirmed)
            assert not await life.confirm_removed(confirmed)
            assert await counts(sessions) == (1, 0, 0, 1)
            assert not (await life.get(OWNER, view.id)).cleanup_pending
            assert not await life.record_observation(
                replace(
                    observation, event_version=3, state=SandboxState.READY, resource_present=True
                )
            )

    asyncio.run(exercise())


def test_retention_never_ages_out_pending_cleanup_or_undelivered_commands(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import OrchestratorObservation

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            spec = await upload(artifacts)
            cleaned = await life.submit(OWNER, "cleaned", spec)
            pending = await life.submit(OWNER, "pending", spec)
            await life.terminate(OWNER, pending.id, TerminationReason.REQUESTED)
            boot = uuid4()
            assert hasattr(life, "purge_retained"), "safe retention missing"
            await life.bind_orchestrator_boot(OWNER, config.identity, cleaned.id, 1, None, boot)
            await life.confirm_removed(
                OrchestratorObservation(
                    cleaned.id,
                    OWNER,
                    config.identity,
                    1,
                    boot,
                    1,
                    datetime.now(UTC),
                    SandboxState.TERMINATED,
                    False,
                    True,
                    cleanup_pending=False,
                )
            )
            async with sessions.begin() as session:
                await session.exec(
                    update(Sandbox)
                    .where(col(Sandbox.id) == cleaned.id)
                    .values(cleaned_at=func.clock_timestamp() - timedelta(days=8))
                )
                await session.exec(
                    update(Outbox).values(created_at=func.clock_timestamp() - timedelta(days=9))
                )
            await life.purge_retained()
            assert await life.get(OWNER, cleaned.id)  # undelivered command holds the record
            async with sessions.begin() as session:
                await session.exec(
                    update(Outbox)
                    .where(col(Outbox.sandbox_id) == cleaned.id)
                    .values(delivered_at=func.clock_timestamp() - timedelta(days=8))
                )
            await life.purge_retained()
            with pytest.raises(BoxieError):
                await life.get(OWNER, cleaned.id)
            assert (await life.get(OWNER, pending.id)).cleanup_pending
            assert await counts(sessions) == (1, 1, 1, 2)

    asyncio.run(exercise())


def test_unhealthy_admission_preserves_retry_status_and_stop(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            spec = await upload(artifacts)
            view = await life.submit(OWNER, "accepted", spec)
            async with sessions.begin() as session:
                await session.exec(
                    update(Outbox).values(created_at=func.clock_timestamp() - timedelta(seconds=31))
                )
            assert not await life.ready()
            assert await life.submit(OWNER, "accepted", spec) == view
            assert await life.get(OWNER, view.id) == view
            with pytest.raises(BoxieError) as failure:
                await life.submit(OWNER, "new", spec)
            assert failure.value.status == 503
            assert (
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            ).cleanup_pending

    asyncio.run(exercise())


def test_published_orphan_and_retirement_deletion_retry(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        import os
        import shutil
        import time

        async with database(postgres_dsn, tmp_path) as (_, artifacts, sessions, _):
            spec = await upload(artifacts)
            path = await artifacts.path(OWNER, spec.artifact_id)
            real_remove = shutil.rmtree

            def unavailable(path: Any, *args: Any, **kwargs: Any) -> None:
                raise OSError("unavailable storage")

            with monkeypatch.context() as patch:
                patch.setattr(shutil, "rmtree", unavailable)
                await artifacts.retire(OWNER, spec.artifact_id)
            assert path.is_dir()
            assert await artifacts.collect() == 1
            assert not path.exists()
            content = bundle()

            async def chunks() -> AsyncIterator[bytes]:
                yield content

            with pytest.raises(BoxieError) as retired:
                await artifacts.put(
                    OWNER, spec.artifact_id, hashlib.sha256(content).hexdigest(), chunks()
                )
            assert retired.value.status == 409
            # A metadata constraint injects a crash-equivalent publication/commit gap.
            await add_check(
                sessions,
                col(Artifact.consumer_id) != "failure",
                "publication_failure",
                not_valid=True,
            )
            with pytest.raises(IntegrityError) as rejected:
                await artifacts.put(
                    Principal("failure"), uuid4(), hashlib.sha256(content).hexdigest(), chunks()
                )
            assert isinstance(rejected.value.orig, psycopg.errors.CheckViolation)
            assert rejected.value.orig.diag.constraint_name == "publication_failure"
            orphans = [p for p in artifacts.root.iterdir() if p.is_dir()]
            assert (
                len(orphans) == 1
                and (orphans[0] / "files/nested/hello.txt").read_bytes() == b"ordinary text\n"
            )
            os.utime(orphans[0], (time.time() - 7200, time.time() - 7200))
            assert await artifacts.collect_orphans() == 1
            assert not orphans[0].exists()
            assert real_remove is shutil.rmtree

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["submit", "get", "terminate"])
def test_exhausted_pool_is_503_without_lifecycle_writes(
    postgres_dsn: str, tmp_path: Path, operation: str
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, original_sessions, _):
            spec = await upload(artifacts)
            original = await life.submit(OWNER, "original", spec)
            async with session_database(
                postgres_dsn, pool_size=1, pool_timeout=0.05
            ) as limited_sessions:
                life.sessions = limited_sessions
                async with limited_sessions() as held:
                    await held.exec(select(1))
                    with pytest.raises(BoxieError) as unavailable:
                        if operation == "submit":
                            await asyncio.wait_for(life.submit(OWNER, "new", spec), 1)
                        elif operation == "get":
                            await asyncio.wait_for(life.get(OWNER, original.id), 1)
                        else:
                            await asyncio.wait_for(
                                life.terminate(OWNER, original.id, TerminationReason.REQUESTED), 1
                            )
                    assert unavailable.value.status == 503
                assert await counts(original_sessions) == (1, 1, 1, 1)
                assert await life.get(OWNER, original.id) == original
                assert await life.submit(OWNER, "original", spec) == original

    asyncio.run(exercise())


# All operations share consumer_session's classification. Exercise its full
# error table once, then retain the read and stop transaction outcomes separately.
@pytest.mark.parametrize(
    "operation, error_type, unavailable",
    [
        ("submit", psycopg.OperationalError, True),
        ("submit", psycopg.errors.ConnectionFailure, True),
        ("submit", psycopg.errors.AdminShutdown, True),
        ("submit", psycopg.errors.LockNotAvailable, True),
        ("submit", psycopg.errors.SerializationFailure, True),
        ("submit", PoolTimeout, True),
        ("submit", psycopg.errors.CheckViolation, False),
        ("submit", psycopg.errors.UndefinedTable, False),
        ("submit", psycopg.errors.InvalidPassword, False),
        ("submit", ResourceClosedError, False),
        ("get", psycopg.errors.CheckViolation, False),
        ("terminate", psycopg.OperationalError, True),
    ],
)
def test_consumer_database_error_boundary_never_retries(
    postgres_dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_type: type[Exception],
    unavailable: bool,
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            spec = await upload(artifacts)
            original = await life.submit(OWNER, "original", spec)
            calls = 0
            error = error_type("injected database boundary failure")
            injected = DBAPIError(None, None, error) if isinstance(error, psycopg.Error) else error

            class FailedSession(AsyncSession):
                async def __aexit__(
                    self,
                    exc_type: type[BaseException] | None,
                    exc_value: BaseException | None,
                    traceback: TracebackType | None,
                ) -> None:
                    nonlocal calls
                    await super().__aexit__(exc_type, exc_value, traceback)
                    calls += 1
                    # The transaction has committed: caller retries own ambiguous outcomes.
                    raise injected

            failed_sessions = async_sessionmaker(
                bind=sessions.kw["bind"], class_=FailedSession, expire_on_commit=False
            )
            with monkeypatch.context() as patch:
                patch.setattr(life, "sessions", failed_sessions)
                if unavailable:
                    expected: type[Exception] = BoxieError
                else:
                    expected = type(injected)
                with pytest.raises(expected) as failure:
                    if operation == "submit":
                        await life.submit(OWNER, "new", spec)
                    elif operation == "get":
                        await life.get(OWNER, original.id)
                    else:
                        await life.terminate(OWNER, original.id, TerminationReason.REQUESTED)
                if isinstance(failure.value, BoxieError):
                    assert failure.value.status == 503
            assert calls == 1
            expected_counts = (
                (2, 2, 2, 2)
                if operation == "submit"
                else (1, 1, 1, 2)
                if operation == "terminate"
                else (1, 1, 1, 1)
            )
            assert await counts(sessions) == expected_counts
            if operation == "submit":
                # A caller can safely retry the key after the ambiguous response.
                retry = await life.submit(OWNER, "new", spec)
                assert retry.id != original.id
                assert await counts(sessions) == expected_counts

    asyncio.run(exercise())


def test_final_start_guard_serializes_stop_and_validates_durable_request(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            assert importlib.util.find_spec("boxie.persistence.start_guard"), (
                "final-start guard missing"
            )
            from boxie.domain.lifecycle import EnsureRequest
            from boxie.persistence.start_guard import PostgresStartGuard

            view = await life.submit(OWNER, "guard", await upload(artifacts))
            boot = uuid4()
            await life.bind_orchestrator_boot(OWNER, config.identity, view.id, 1, None, boot)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            guard = PostgresStartGuard(sessions, timeout=1)
            entered, release = asyncio.Event(), asyncio.Event()
            starts = 0

            async def start() -> None:
                nonlocal starts
                starts += 1
                entered.set()
                await release.wait()

            assert await guard.start_allowed(request, boot)
            assert not await guard.start_allowed(
                replace(request, principal=Principal("other")), boot
            )
            assert not await guard.start_allowed(request, uuid4())
            assert not await guard.start_allowed(
                replace(request, spec=replace(request.spec, env={"GREETING": "hello"})), boot
            )
            task = asyncio.create_task(guard.run_start_if_allowed(request, boot, start))
            await entered.wait()
            stop = asyncio.create_task(life.terminate(OWNER, view.id, TerminationReason.REQUESTED))
            await asyncio.sleep(0.05)
            assert not stop.done(), "stop must share final-start row serialization"
            release.set()
            assert await task
            await stop
            assert not await guard.start_allowed(request, boot)
            assert not await guard.run_start_if_allowed(request, boot, start)
            assert starts == 1
            assert await counts(sessions) == (1, 1, 1, 2)

    asyncio.run(exercise())


def test_start_guard_timeout_and_lost_connection_retain_reservations(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "timeout", await upload(artifacts))
            boot = uuid4()
            await life.bind_orchestrator_boot(OWNER, config.identity, view.id, 1, None, boot)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            guard = PostgresStartGuard(sessions, timeout=0.05)

            async def stuck() -> None:
                await asyncio.Event().wait()

            with pytest.raises(TimeoutError):
                await guard.run_start_if_allowed(request, boot, stuck)
            assert await counts(sessions) == (1, 1, 1, 1)

            # A real server-side connection termination during the callback models
            # guard loss; the row lock cannot promise atomic DB/Docker execution.
            async def disconnect() -> None:
                activity = Table(
                    "pg_stat_activity",
                    MetaData(),
                    Column("pid", Integer),
                    Column("datname"),
                    Column("state"),
                    Column("query"),
                )
                async with sessions.begin() as other:
                    pid = await other.scalar(
                        select(activity.c.pid).where(
                            activity.c.datname == func.current_database(),
                            activity.c.pid != func.pg_backend_pid(),
                            activity.c.state == "idle in transaction",
                            activity.c.query.like("%sandbox_artifact_refs%"),
                        )
                    )
                    assert pid is not None
                    assert await other.scalar(select(func.pg_terminate_backend(pid)))

            guard.timeout = 1
            with pytest.raises(DBAPIError):
                await guard.run_start_if_allowed(request, boot, disconnect)
            assert await counts(sessions) == (1, 1, 1, 1)
            await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            assert not await guard.run_start_if_allowed(request, boot, stuck)

    asyncio.run(exercise())


def test_durable_orchestrator_and_postgres_release_only_after_reconciled_absence(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard
        from boxie.sandbox.execution.orchestrator import DurableOrchestrator
        from boxie_test_support.durable import Daemon
        from lifecycle_support import settled

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "real-persistence", await upload(artifacts))
            clock = Clock()
            clock.now = datetime.now(UTC)
            daemon = Daemon()
            orchestrator = DurableOrchestrator(
                config, daemon, artifacts.path, PostgresStartGuard(sessions), clock, clock.monotonic
            )
            await life.bind_orchestrator_boot(
                OWNER, config.identity, view.id, 1, None, orchestrator.boot_id
            )
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            await launch_execution(orchestrator, request)
            observation = await settled(orchestrator, request.ref, SandboxState.READY)
            assert await life.record_observation(observation)
            assert await counts(sessions) == (1, 1, 1, 1)
            daemon.unreachable = True
            pending = await orchestrator.remove(request.ref, TerminationReason.REQUESTED)
            assert pending.cleanup_pending
            assert await life.record_observation(pending)
            assert await counts(sessions) == (1, 1, 1, 1)
            daemon.unreachable = False
            await orchestrator.reconcile()
            removed = await orchestrator.inspect(request.ref)
            assert removed.removal_confirmed
            assert await life.confirm_removed(removed)
            assert await counts(sessions) == (1, 0, 0, 1)
            await orchestrator.close()

    asyncio.run(exercise())


def test_start_guard_requires_retained_artifact_reference(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "reference", await upload(artifacts))
            boot = uuid4()
            await life.bind_orchestrator_boot(OWNER, config.identity, view.id, 1, None, boot)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            guard = PostgresStartGuard(sessions)
            assert await guard.start_allowed(request, boot)
            async with sessions.begin() as session:
                await session.exec(
                    delete(SandboxArtifactRef).where(col(SandboxArtifactRef.sandbox_id) == view.id)
                )
            assert (await artifacts.path(OWNER, request.spec.artifact_id)).is_dir()
            assert not await guard.start_allowed(request, boot)

            async def forbidden_start() -> None:
                raise AssertionError("missing reference must prevent start")

            assert not await guard.run_start_if_allowed(request, boot, forbidden_start)

    asyncio.run(exercise())


def test_journal_outage_expires_container_but_retains_sql_accounting_until_durable_confirmation(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard
        from boxie.sandbox.execution.orchestrator import DurableOrchestrator
        from boxie_test_support.durable import Daemon
        from lifecycle_support import settled

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "journal-outage", await upload(artifacts))
            clock = Clock()
            clock.now = datetime.now(UTC)
            daemon = Daemon()
            orchestrator = DurableOrchestrator(
                config, daemon, artifacts.path, PostgresStartGuard(sessions), clock, clock.monotonic
            )
            await life.bind_orchestrator_boot(
                OWNER, config.identity, view.id, 1, None, orchestrator.boot_id
            )
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            await launch_execution(orchestrator, request)
            ready = await settled(orchestrator, request.ref, SandboxState.READY)
            assert await life.record_observation(ready)
            await orchestrator.start()
            original_save = orchestrator.journal.save

            def unavailable() -> None:
                raise OSError("persistent journal storage outage")

            monkeypatch.setattr(orchestrator.journal, "save", unavailable)
            try:
                clock.advance(90)
                for _ in range(200):
                    if daemon.deleted_ids:
                        break
                    await asyncio.sleep(0.001)
                assert daemon.deleted_ids == [ready.container_id]
                pending = await orchestrator.inspect(request.ref)
                assert pending.cleanup_pending and not pending.removal_confirmed
                assert not orchestrator.execution_ready
                assert await life.record_observation(pending)
                assert await counts(sessions) == (1, 1, 1, 1)
                with pytest.raises(ValueError, match="explicit physical removal"):
                    await life.confirm_removed(pending)
                monkeypatch.setattr(orchestrator.journal, "save", original_save)
                removed = await settled(orchestrator, request.ref, SandboxState.TERMINATED)
                assert removed.removal_confirmed and not removed.cleanup_pending
                assert await life.confirm_removed(removed)
                assert await counts(sessions) == (1, 0, 0, 1)
            finally:
                monkeypatch.setattr(orchestrator.journal, "save", original_save)
                await orchestrator.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("purged", [False, True])
def test_terminal_receipt_survives_prior_boot_and_retry_row_purge(
    postgres_dsn: str, tmp_path: Path, purged: bool
) -> None:
    from datetime import timedelta

    from boxie.domain.lifecycle import EnsureRequest
    from boxie.persistence.start_guard import PostgresStartGuard
    from boxie.sandbox.execution.orchestrator import DurableOrchestrator
    from boxie_test_support.durable import Daemon
    from lifecycle_support import settled

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "receipt", await upload(artifacts))
            clock, daemon = Clock(), Daemon()
            clock.now = datetime.now(UTC)
            guard = PostgresStartGuard(sessions)
            orchestrator = DurableOrchestrator(
                config, daemon, artifacts.path, guard, clock, clock.monotonic
            )
            await life.bind_orchestrator_boot(
                OWNER, config.identity, view.id, 1, None, orchestrator.boot_id
            )
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            await launch_execution(orchestrator, request)
            await settled(orchestrator, request.ref, SandboxState.READY)
            await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            observation = await orchestrator.remove(request.ref, TerminationReason.REQUESTED)
            assert await life.record_observation(observation)
            assert await guard.terminal_receipt(request)

            assert not await guard.terminal_receipt(
                replace(request, principal=Principal("foreign"))
            )
            assert not await guard.terminal_receipt(
                replace(request, absolute_deadline=request.absolute_deadline + timedelta(seconds=1))
            )
            assert await guard.initialization(request, orchestrator.boot_id) is None
            await orchestrator.close()
            if purged:
                async with sessions.begin() as session:
                    await session.exec(
                        update(Outbox)
                        .where(col(Outbox.sandbox_id) == view.id)
                        .values(delivered_at=func.clock_timestamp() - timedelta(days=8))
                    )
                    await session.exec(
                        update(Sandbox)
                        .where(col(Sandbox.id) == view.id)
                        .values(cleaned_at=func.clock_timestamp() - timedelta(days=8))
                    )
                await life.purge_retained()
            restarted = DurableOrchestrator(
                config, daemon, artifacts.path, guard, clock, clock.monotonic
            )
            assert restarted.boot_id != orchestrator.boot_id
            await restarted.compact()
            assert not restarted.records
            with pytest.raises(BoxieError):
                await launch_execution(restarted, request)
            assert not restarted.records and not restarted.runtimes
            await restarted.close()

    asyncio.run(exercise())


def test_failed_restart_journal_projection_waits_for_local_inspection(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxie.domain.lifecycle import EnsureRequest
    from boxie.persistence.start_guard import PostgresStartGuard
    from boxie.sandbox.execution.orchestrator import DurableOrchestrator
    from boxie.temporal.activities import LifecycleActivities
    from boxie_test_support.durable import Daemon
    from temporalio.exceptions import ApplicationError

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "restart-journal", await upload(artifacts))
            clock, daemon = Clock(), Daemon()
            clock.now = datetime.now(UTC)
            guard = PostgresStartGuard(sessions)
            original = DurableOrchestrator(
                config, daemon, artifacts.path, guard, clock, clock.monotonic
            )
            first = LifecycleActivities(life, original)
            await first.ensure_sandbox(str(view.id))
            old = await original.inspect(
                EnsureRequest.from_durable(await life.load_request(view.id)).ref
            )
            replacement = DurableOrchestrator(
                config, daemon, artifacts.path, guard, clock, clock.monotonic
            )
            save = replacement.journal.save

            def unavailable() -> None:
                raise OSError("journal unavailable")

            try:
                monkeypatch.setattr(replacement.journal, "save", unavailable)
                await replacement.reconcile()
                assert not daemon.resources
                assert (await life.get(OWNER, view.id)).state == SandboxState.READY
                activities = LifecycleActivities(life, replacement)
                facts = await activities.inspect_and_renew(str(view.id))
                assert facts.desired_stop and not facts.cleaned
                assert not await life.record_observation(old)
                with pytest.raises(ApplicationError, match="cleanup pending"):
                    await activities.remove_sandbox(str(view.id))
                assert (await counts(sessions))[1:3] == (1, 1)
                monkeypatch.setattr(replacement.journal, "save", save)
                await activities.remove_sandbox(str(view.id))
                assert (await counts(sessions))[1:3] == (0, 0)
            finally:
                monkeypatch.setattr(replacement.journal, "save", save)
                await replacement.close()
                await original.close()

    asyncio.run(exercise())


def test_command_recovery_is_scoped_to_the_physical_owner(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from boxie.persistence.outbox import PostgresOutbox

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            spec = await upload(artifacts)
            first = await life.submit(OWNER, "owner-one", spec)
            foreign_identity = replace(config.identity, host_id="another-host")
            foreign = PostgresLifecycle(
                sessions, life.resolver, foreign_identity, life.dependencies
            )
            second = await foreign.submit(OWNER, "owner-two", spec)
            pending = await PostgresOutbox(sessions, config.identity).pending()
            assert [event.sandbox_id for event in pending] == [first.id]
            assert not await PostgresOutbox(sessions, config.identity).pending(second.id)

    asyncio.run(exercise())
