"""Exercise real transactional admission, crash boundaries and ownership accounting."""

import asyncio
import hashlib
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.models import (
    AgentIdentity,
    Principal,
    SandboxSpec,
    SandboxState,
    TerminationReason,
)
from boxie_test_support.agent import Clock, bundle, configuration
from boxie_test_support.terminal_network import network
from integration.conftest import postgres_dsn as postgres_dsn
from integration.conftest import postgres_server as postgres_server
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, TooManyRequests

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).parents[1] / "src/boxie/persistence/migrations"
OWNER = Principal("one")


def adapters() -> tuple[Any, Any, Any]:
    assert importlib.util.find_spec("boxie.persistence.postgres") is not None, (
        "durable admission missing"
    )
    from boxie.persistence.migrate import migrate
    from boxie.persistence.postgres import PostgresLifecycle
    from boxie.persistence.postgres_artifacts import PostgresArtifactStore

    return migrate, PostgresLifecycle, PostgresArtifactStore


@asynccontextmanager
async def database(dsn: str, root: Path) -> AsyncIterator[tuple[Any, Any, Any, Any]]:
    migrate, lifecycle_type, artifact_type = adapters()
    await asyncio.to_thread(migrate, dsn, MIGRATIONS)
    config = configuration(root)
    from boxie.sandbox.client import AgentClient

    async with network(root / "resolver") as harness:
        async with AgentClient(harness.agent_url, b"agent-test-secret") as resolver:
            async with AsyncConnectionPool(
                dsn, min_size=1, max_size=24, kwargs={"row_factory": dict_row}
            ) as pool:

                async def healthy() -> bool:
                    return True

                lifecycle = lifecycle_type(pool, resolver, config.identity, healthy)
                artifacts = artifact_type(pool, root / "durable-artifacts")
                yield lifecycle, artifacts, pool, config


async def upload(artifacts: Any) -> SandboxSpec:
    content = bundle()

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    result = await artifacts.put(OWNER, uuid4(), hashlib.sha256(content).hexdigest(), chunks())
    return SandboxSpec(result.artifact.id, "shell", ("/bin/sh",), {})


async def counts(pool: Any) -> tuple[int, int, int, int]:
    """Admission/slot/reference/command counts; observation notifications tested separately."""
    async with pool.connection() as conn:
        result = []
        for query in (
            "SELECT count(*) AS n FROM sandboxes",
            "SELECT count(*) AS n FROM capacity_slots WHERE sandbox_id IS NOT NULL",
            "SELECT count(*) AS n FROM sandbox_artifact_refs",
            "SELECT count(*) AS n FROM outbox WHERE kind IN ('start','stop')",
        ):
            result.append((await (await conn.execute(query)).fetchone())["n"])
        return tuple(result)


def test_nine_concurrent_creates_reserve_exactly_eight(postgres_dsn: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, _):
            spec = await upload(artifacts)
            result = await asyncio.gather(
                *(life.submit(OWNER, str(i), spec) for i in range(9)), return_exceptions=True
            )
            assert sum(not isinstance(item, Exception) for item in result) == 8
            errors = [item for item in result if isinstance(item, BoxieError)]
            assert len(errors) == 1 and errors[0].status == 429
            assert await counts(pool) == (8, 8, 8, 8)

    asyncio.run(exercise())


def test_identical_retries_old_snapshot_and_conflicting_key(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        import json

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            spec = await upload(artifacts)
            views = await asyncio.gather(*(life.submit(OWNER, "same", spec) for _ in range(20)))
            assert len({item.id for item in views}) == 1
            assert await counts(pool) == (1, 1, 1, 1)
            first = await life.load_request(views[0].id)
            data = json.loads(config.profiles_file.read_text())
            data["shell"]["image_digest"] = "example.org/shell@sha256:" + "4" * 64
            config.profiles_file.write_text(json.dumps(data))
            # A fresh trusted resolver reads the operator's new profile file.
            from boxie.sandbox.client import AgentClient

            async with network(tmp_path / "reloaded-resolver") as harness:
                harness.config.profiles_file.write_text(config.profiles_file.read_text())
                harness.agent.profiles.reload()
                async with AgentClient(harness.agent_url, b"agent-test-secret") as resolver:
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
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, _):
            view = await life.submit(OWNER, "stop", await upload(artifacts))
            stopped = await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            assert stopped.state == SandboxState.TERMINATING and stopped.cleanup_pending
            assert (await life.load_request(view.id)).desired_stop
            await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            assert await counts(pool) == (1, 1, 1, 2)

    asyncio.run(exercise())


def test_retirement_races_admission_without_losing_files(postgres_dsn: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, _):
            spec = await upload(artifacts)
            outcomes = await asyncio.gather(
                life.submit(OWNER, "race", spec),
                artifacts.retire(OWNER, spec.artifact_id),
                return_exceptions=True,
            )
            view: Any = outcomes[0]
            if isinstance(view, BoxieError):
                assert view.status == 409
                assert await counts(pool) == (0, 0, 0, 0)
            else:
                assert not isinstance(view, Exception)
                assert (await artifacts.path(OWNER, spec.artifact_id)).is_dir()
                assert await counts(pool) == (1, 1, 1, 1)
            with pytest.raises(BoxieError):
                await life.submit(OWNER, "after", spec)

    asyncio.run(exercise())


def test_outbox_failure_rolls_back_all_admission_writes(postgres_dsn: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, _):
            spec = await upload(artifacts)
            async with pool.connection() as conn:
                await conn.execute(
                    "ALTER TABLE outbox ADD CONSTRAINT injected_failure CHECK (kind <> 'start')"
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                await life.submit(OWNER, "rollback", spec)
            assert await counts(pool) == (0, 0, 0, 0)

    asyncio.run(exercise())


def test_migrations_are_checksummed_atomic_and_serialized(
    postgres_dsn: str, tmp_path: Path
) -> None:
    migrate, _, _ = adapters()
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    first = migrations / "001_initial.sql"
    first.write_text("CREATE TABLE durable_test (id integer PRIMARY KEY);")

    async def concurrent() -> None:
        await asyncio.gather(
            *(asyncio.to_thread(migrate, postgres_dsn, migrations) for _ in range(4))
        )

    asyncio.run(concurrent())
    first.write_text("CREATE TABLE changed (id integer);")
    with pytest.raises(ValueError, match="checksum"):
        migrate(postgres_dsn, migrations)
    first.write_text("CREATE TABLE durable_test (id integer PRIMARY KEY);")
    (migrations / "002_failed.sql").write_text(
        "CREATE TABLE half_applied (id integer); SELECT 1/0;"
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(postgres_dsn, migrations)
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute("SELECT to_regclass('half_applied')").fetchone() == (None,)
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)


def test_only_fenced_confirmed_removal_releases_capacity_and_references(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from datetime import UTC, datetime

        from boxie.domain.lifecycle import AgentObservation

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            view = await life.submit(OWNER, "fence", await upload(artifacts))
            assert not view.cleanup_pending
            boot = uuid4()
            assert hasattr(life, "bind_agent_boot"), "authenticated boot binding missing"
            assert await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, boot)
            observation = AgentObservation(
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
            assert await counts(pool) == (1, 1, 1, 1)
            assert (await life.get(OWNER, view.id)).cleanup_pending
            confirmed = replace(
                observation, event_version=2, removal_confirmed=True, cleanup_pending=False
            )
            for bad in (
                replace(confirmed, boot_id=uuid4()),
                replace(confirmed, generation=2),
                replace(confirmed, principal=Principal("other")),
                replace(confirmed, identity=AgentIdentity(uuid4(), config.identity.host_id)),
                replace(confirmed, event_version=1),
            ):
                assert not await life.confirm_removed(bad)
            assert await counts(pool) == (1, 1, 1, 1)
            assert await life.confirm_removed(confirmed)
            assert not await life.confirm_removed(confirmed)
            assert await counts(pool) == (1, 0, 0, 1)
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
        from datetime import UTC, datetime

        from boxie.domain.lifecycle import AgentObservation

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            spec = await upload(artifacts)
            cleaned = await life.submit(OWNER, "cleaned", spec)
            pending = await life.submit(OWNER, "pending", spec)
            await life.terminate(OWNER, pending.id, TerminationReason.REQUESTED)
            boot = uuid4()
            assert hasattr(life, "purge_retained"), "safe retention missing"
            await life.bind_agent_boot(OWNER, config.identity, cleaned.id, 1, None, boot)
            await life.confirm_removed(
                AgentObservation(
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
            async with pool.connection() as conn:
                await conn.execute(
                    "UPDATE sandboxes SET cleaned_at=clock_timestamp()-interval '8 days' WHERE id=%s",
                    (cleaned.id,),
                )
                await conn.execute(
                    "UPDATE outbox SET created_at=clock_timestamp()-interval '9 days'"
                )
            await life.purge_retained()
            assert await life.get(OWNER, cleaned.id)  # undelivered command holds the record
            async with pool.connection() as conn:
                await conn.execute(
                    "UPDATE outbox SET delivered_at=clock_timestamp()-interval '8 days' WHERE sandbox_id=%s",
                    (cleaned.id,),
                )
            await life.purge_retained()
            with pytest.raises(BoxieError):
                await life.get(OWNER, cleaned.id)
            assert (await life.get(OWNER, pending.id)).cleanup_pending
            assert await counts(pool) == (1, 1, 1, 2)

    asyncio.run(exercise())


def test_unhealthy_admission_preserves_retry_status_and_stop(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, _):
            spec = await upload(artifacts)
            view = await life.submit(OWNER, "accepted", spec)
            async with pool.connection() as conn:
                await conn.execute(
                    "UPDATE outbox SET created_at=clock_timestamp()-interval '31 seconds'"
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

        async with database(postgres_dsn, tmp_path) as (_, artifacts, pool, _):
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
            async with pool.connection() as conn:
                await conn.execute(
                    "ALTER TABLE artifacts ADD CONSTRAINT publication_failure CHECK (consumer_id <> 'failure') NOT VALID"
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                await artifacts.put(
                    Principal("failure"), uuid4(), hashlib.sha256(content).hexdigest(), chunks()
                )
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
        async with database(postgres_dsn, tmp_path) as (life, artifacts, original_pool, _):
            spec = await upload(artifacts)
            original = await life.submit(OWNER, "original", spec)
            async with AsyncConnectionPool(
                postgres_dsn,
                min_size=1,
                max_size=1,
                timeout=0.05,
                kwargs={"row_factory": dict_row},
            ) as limited_pool:
                await limited_pool.wait()
                life.pool = limited_pool
                async with limited_pool.connection():
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
                assert await counts(original_pool) == (1, 1, 1, 1)
                assert await life.get(OWNER, original.id) == original
                assert await life.submit(OWNER, "original", spec) == original

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["submit", "get", "terminate"])
@pytest.mark.parametrize(
    "error_type, unavailable",
    [
        (psycopg.OperationalError, True),
        (psycopg.errors.ConnectionFailure, True),
        (psycopg.errors.AdminShutdown, True),
        (psycopg.errors.LockNotAvailable, True),
        (psycopg.errors.SerializationFailure, True),
        (TooManyRequests, True),
        (psycopg.errors.CheckViolation, False),
        (psycopg.errors.UndefinedTable, False),
        (psycopg.errors.InvalidPassword, False),
        (PoolClosed, False),
    ],
)
def test_consumer_database_error_boundary_never_retries(
    postgres_dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_type: type[psycopg.Error],
    unavailable: bool,
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, _):
            spec = await upload(artifacts)
            original = await life.submit(OWNER, "original", spec)
            calls = 0
            real_connection = pool.connection

            @asynccontextmanager
            async def failed_connection() -> AsyncIterator[Any]:
                nonlocal calls
                calls += 1
                # Failure on context exit models loss of acknowledgement after
                # a committed operation, where replay must be left to the caller.
                async with real_connection() as conn:
                    yield conn
                raise error_type("injected database boundary failure")

            with monkeypatch.context() as patch:
                patch.setattr(pool, "connection", failed_connection)
                if unavailable:
                    expected: type[Exception] = BoxieError
                else:
                    expected = error_type
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
            assert await counts(pool) == expected_counts
            if operation == "submit":
                # A caller can safely retry the key after the ambiguous response.
                retry = await life.submit(OWNER, "new", spec)
                assert retry.id != original.id
                assert await counts(pool) == expected_counts

    asyncio.run(exercise())


def test_final_start_guard_serializes_stop_and_validates_durable_request(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            assert importlib.util.find_spec("boxie.persistence.start_guard"), (
                "final-start guard missing"
            )
            from boxie.domain.lifecycle import EnsureRequest
            from boxie.persistence.start_guard import PostgresStartGuard

            view = await life.submit(OWNER, "guard", await upload(artifacts))
            boot = uuid4()
            await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, boot)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            guard = PostgresStartGuard(pool, timeout=1)
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
            assert await counts(pool) == (1, 1, 1, 2)

    asyncio.run(exercise())


def test_start_guard_timeout_and_lost_connection_retain_reservations(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            view = await life.submit(OWNER, "timeout", await upload(artifacts))
            boot = uuid4()
            await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, boot)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            guard = PostgresStartGuard(pool, timeout=0.05)

            async def stuck() -> None:
                await asyncio.Event().wait()

            with pytest.raises(TimeoutError):
                await guard.run_start_if_allowed(request, boot, stuck)
            assert await counts(pool) == (1, 1, 1, 1)

            # A real server-side connection termination during the callback models
            # guard loss; the row lock cannot promise atomic DB/Docker execution.
            async def disconnect() -> None:
                async with pool.connection() as other:
                    row = await (
                        await other.execute("""SELECT pid FROM pg_stat_activity
                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                        AND state='idle in transaction' AND query LIKE '%sandbox_artifact_refs%'""")
                    ).fetchone()
                    assert row is not None
                    await other.execute("SELECT pg_terminate_backend(%s)", (row["pid"],))

            guard.timeout = 1
            with pytest.raises(psycopg.OperationalError):
                await guard.run_start_if_allowed(request, boot, disconnect)
            assert await counts(pool) == (1, 1, 1, 1)
            await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            assert not await guard.run_start_if_allowed(request, boot, stuck)

    asyncio.run(exercise())


def test_durable_agent_and_postgres_release_only_after_reconciled_absence(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from datetime import UTC, datetime

        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard
        from boxie.sandbox.lifecycle import DurableAgent
        from test_agent_lifecycle import Daemon, settled

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            view = await life.submit(OWNER, "real-persistence", await upload(artifacts))
            clock = Clock()
            clock.now = datetime.now(UTC)
            daemon = Daemon()
            agent = DurableAgent(
                config, daemon, artifacts.path, PostgresStartGuard(pool), clock, clock.monotonic
            )
            await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, agent.boot_id)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            await agent.ensure(request)
            observation = await settled(agent, request.ref, SandboxState.READY)
            assert await life.record_observation(observation)
            assert await counts(pool) == (1, 1, 1, 1)
            daemon.unreachable = True
            pending = await agent.remove(request.ref, TerminationReason.REQUESTED)
            assert pending.cleanup_pending
            assert await life.record_observation(pending)
            assert await counts(pool) == (1, 1, 1, 1)
            daemon.unreachable = False
            await agent.reconcile()
            removed = await agent.inspect(request.ref)
            assert removed.removal_confirmed
            assert await life.confirm_removed(removed)
            assert await counts(pool) == (1, 0, 0, 1)
            await agent.close()

    asyncio.run(exercise())


def test_start_guard_requires_retained_artifact_reference(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            view = await life.submit(OWNER, "reference", await upload(artifacts))
            boot = uuid4()
            await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, boot)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            guard = PostgresStartGuard(pool)
            assert await guard.start_allowed(request, boot)
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM sandbox_artifact_refs WHERE sandbox_id=%s", (view.id,)
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
        from datetime import UTC, datetime

        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard
        from boxie.sandbox.lifecycle import DurableAgent
        from test_agent_lifecycle import Daemon, settled

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            view = await life.submit(OWNER, "journal-outage", await upload(artifacts))
            clock = Clock()
            clock.now = datetime.now(UTC)
            daemon = Daemon()
            agent = DurableAgent(
                config, daemon, artifacts.path, PostgresStartGuard(pool), clock, clock.monotonic
            )
            await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, agent.boot_id)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            await agent.ensure(request)
            ready = await settled(agent, request.ref, SandboxState.READY)
            assert await life.record_observation(ready)
            await agent.start()
            original_save = agent.journal.save

            def unavailable() -> None:
                raise OSError("persistent journal storage outage")

            monkeypatch.setattr(agent.journal, "save", unavailable)
            try:
                clock.advance(90)
                for _ in range(200):
                    if daemon.deleted_ids:
                        break
                    await asyncio.sleep(0.001)
                assert daemon.deleted_ids == [ready.container_id]
                pending = await agent.inspect(request.ref)
                assert pending.cleanup_pending and not pending.removal_confirmed
                assert not agent.execution_ready
                assert await life.record_observation(pending)
                assert await counts(pool) == (1, 1, 1, 1)
                with pytest.raises(ValueError, match="explicit physical removal"):
                    await life.confirm_removed(pending)
                monkeypatch.setattr(agent.journal, "save", original_save)
                removed = await settled(agent, request.ref, SandboxState.TERMINATED)
                assert removed.removal_confirmed and not removed.cleanup_pending
                assert await life.confirm_removed(removed)
                assert await counts(pool) == (1, 0, 0, 1)
            finally:
                monkeypatch.setattr(agent.journal, "save", original_save)
                await agent.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("purged", [False, True])
def test_terminal_receipt_survives_prior_boot_and_retry_row_purge(
    postgres_dsn: str, tmp_path: Path, purged: bool
) -> None:
    from datetime import UTC, datetime, timedelta

    from boxie.domain.lifecycle import EnsureRequest
    from boxie.persistence.start_guard import PostgresStartGuard
    from boxie.sandbox.lifecycle import DurableAgent
    from boxie_test_support.durable import Daemon
    from test_agent_lifecycle import settled

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            view = await life.submit(OWNER, "receipt", await upload(artifacts))
            clock, daemon = Clock(), Daemon()
            clock.now = datetime.now(UTC)
            guard = PostgresStartGuard(pool)
            agent = DurableAgent(config, daemon, artifacts.path, guard, clock, clock.monotonic)
            await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, agent.boot_id)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            await agent.ensure(request)
            await settled(agent, request.ref, SandboxState.READY)
            await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            observation = await agent.remove(request.ref, TerminationReason.REQUESTED)
            assert await life.record_observation(observation)
            assert await guard.terminal_receipt(request)
            from dataclasses import replace

            assert not await guard.terminal_receipt(
                replace(request, principal=Principal("foreign"))
            )
            assert not await guard.terminal_receipt(
                replace(request, absolute_deadline=request.absolute_deadline + timedelta(seconds=1))
            )
            assert await guard.initialization(request, agent.boot_id) is None
            await agent.close()
            if purged:
                async with pool.connection() as conn:
                    await conn.execute(
                        "UPDATE outbox SET delivered_at=clock_timestamp()-interval '8 days' WHERE sandbox_id=%s",
                        (view.id,),
                    )
                    await conn.execute(
                        "UPDATE sandboxes SET cleaned_at=clock_timestamp()-interval '8 days' WHERE id=%s",
                        (view.id,),
                    )
                await life.purge_retained()
            restarted = DurableAgent(config, daemon, artifacts.path, guard, clock, clock.monotonic)
            assert restarted.boot_id != agent.boot_id
            await restarted.compact()
            assert not restarted.records
            with pytest.raises(BoxieError):
                await restarted.ensure(request)
            assert not restarted.records and not restarted.locks
            await restarted.close()

    asyncio.run(exercise())


def test_failed_restart_journal_projection_waits_for_authenticated_inspection(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed save emits no push; the next worker inspection repairs SQL truth."""

    async def exercise() -> None:
        from datetime import UTC, datetime

        import httpx
        from boxie.domain.lifecycle import EnsureRequest
        from boxie.persistence.start_guard import PostgresStartGuard
        from boxie.sandbox.durable_client import DurableAgentClient
        from boxie.sandbox.durable_server import create_durable_agent_app
        from boxie.sandbox.lifecycle import DurableAgent
        from boxie.sandbox.reporting import ObservationReporter
        from boxie.temporal.activities import LifecycleActivities
        from fastapi import Request, Response
        from starlette.middleware.base import RequestResponseEndpoint
        from temporalio.exceptions import ApplicationError
        from test_agent_lifecycle import Daemon, settled

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            view = await life.submit(OWNER, "restart-journal", await upload(artifacts))
            clock, daemon = Clock(), Daemon()
            clock.now = datetime.now(UTC)
            guard = PostgresStartGuard(pool)
            original = DurableAgent(config, daemon, artifacts.path, guard, clock, clock.monotonic)
            await life.bind_agent_boot(OWNER, config.identity, view.id, 1, None, original.boot_id)
            request = EnsureRequest.from_durable(await life.load_request(view.id))
            await original.ensure(request)
            ready = await settled(original, request.ref, SandboxState.READY)
            assert await life.record_observation(ready)
            journal = original.journal.path.read_bytes()

            def unavailable() -> None:
                raise OSError("persistent journal storage outage")

            agents = [original]
            try:
                for restart in range(2):
                    agent = DurableAgent(
                        config, daemon, artifacts.path, guard, clock, clock.monotonic
                    )
                    agents.append(agent)
                    monkeypatch.setattr(agent.journal, "save", unavailable)
                    reporter = ObservationReporter("http://api", b"restart-secret")
                    app = create_durable_agent_app(
                        agent, artifacts, credential=b"restart-secret", reporter=reporter
                    )
                    entered, release = asyncio.Event(), asyncio.Event()

                    @app.middleware("http")
                    async def gate_removal(
                        request: Request,
                        next: RequestResponseEndpoint,
                        entered: asyncio.Event = entered,
                        release: asyncio.Event = release,
                    ) -> Response:
                        if request.url.path.endswith("/remove"):
                            entered.set()
                            await release.wait()
                        return await next(request)

                    try:
                        # start() reconciles before serving; omit its periodic sweep to
                        # place the worker inspection deterministically after the assertion.
                        await agent.reconcile()
                        assert not daemon.resources and len(daemon.deleted_ids) == 1
                        assert not agent.execution_ready
                        assert original.journal.path.read_bytes() == journal
                        pending = await agent.inspect(request.ref)
                        assert pending.cleanup_pending and not pending.removal_confirmed
                        assert pending.boot_id != ready.boot_id
                        assert not reporter.latest  # Failed saves cannot publish finality.
                        assert await counts(pool) == (1, 1, 1, restart + 1)
                        if restart == 0:
                            # This is the native failure's premature visibility assumption.
                            stale = await life.get(OWNER, view.id)
                            assert stale.state == SandboxState.READY and not stale.cleanup_pending
                        async with DurableAgentClient("http://agent", b"restart-secret") as client:
                            await client._http.aclose()
                            client._http = httpx.AsyncClient(
                                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                base_url="http://agent",
                                headers={"Authorization": "Bearer restart-secret"},
                            )
                            activities = LifecycleActivities(life, client)
                            if restart == 0:
                                facts = await activities.inspect_and_renew(str(view.id))
                                assert facts.desired_stop and not facts.cleaned
                            else:
                                # Binding commits before removal returns. SQL's
                                # durable stop is authoritative even in this interval.
                                removal = asyncio.create_task(
                                    activities.remove_sandbox(str(view.id))
                                )
                                try:
                                    await asyncio.wait_for(entered.wait(), 5)
                                    assert (
                                        await life.load_request(view.id)
                                    ).agent_boot_id == agent.boot_id
                                    in_flight = await life.get(OWNER, view.id)
                                    assert (
                                        in_flight.state == SandboxState.TERMINATING
                                        and in_flight.cleanup_pending
                                    )
                                    async with pool.connection() as conn:
                                        row = await (
                                            await conn.execute(
                                                "SELECT * FROM agent_observations WHERE sandbox_id=%s",
                                                (view.id,),
                                            )
                                        ).fetchone()
                                    assert row is None
                                    assert await counts(pool) == (1, 1, 1, 2)
                                finally:
                                    release.set()
                                with pytest.raises(ApplicationError) as error:
                                    await removal
                                assert error.value.type == "CleanupPending"
                        projected = await life.get(OWNER, view.id)
                        assert (
                            projected.state == SandboxState.TERMINATING
                            and projected.cleanup_pending
                        )
                        assert (await life.load_request(view.id)).agent_boot_id == agent.boot_id
                        assert not await life.record_observation(ready)
                        assert await counts(pool) == (1, 1, 1, restart + 1)
                        print(
                            f"restart {restart + 1}: authenticated boot, pending SQL, slot/ref retained; {'inspection repair' if restart == 0 else 'cleanup retry with no observation while RPC in flight'}"
                        )
                        if restart == 0:
                            await life.terminate(OWNER, view.id, TerminationReason.AGENT_RESTART)
                    finally:
                        await reporter.close()
                recovered = DurableAgent(
                    config, daemon, artifacts.path, guard, clock, clock.monotonic
                )
                agents.append(recovered)
                await recovered.reconcile()
                removed = await recovered.inspect(request.ref)
                assert removed.removal_confirmed
                prior = await life.load_request(view.id)
                assert await life.bind_agent_boot(
                    OWNER, config.identity, view.id, 1, prior.agent_boot_id, recovered.boot_id
                )
                assert await life.confirm_removed(removed)
                assert await counts(pool) == (1, 0, 0, 2)
            finally:
                # Restore saves only during teardown, after every accounting assertion.
                monkeypatch.undo()
                for agent in agents:
                    await agent.close()

    asyncio.run(exercise())
