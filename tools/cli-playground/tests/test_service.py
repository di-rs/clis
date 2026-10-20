"""Service lifecycle boundaries independent of HTTP validation."""

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from cli_playground import service as service_module
from cli_playground.artifacts import stage_archive
from cli_playground.boxie_client import SandboxRequest, SandboxView
from cli_playground.config import Settings
from cli_playground.database import database_url, read_dsn
from cli_playground.models import BuildIdentity, BuildUpdate
from cli_playground.service import CliPlaygroundService
from cli_playground.session_store import SessionBinding, SessionStore
from cli_playground.storage import ArtifactConflictError, EnvironmentStore
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from .boxie_fake import FakeBoxie
from .helpers import MutableClock, binary_bundle, elf_binary


def test_expiry_cleanup_preserves_build_refreshed_during_session_shutdown(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        clock = MutableClock(1_000_000.0)
        service = make_service(docker_settings, clock=clock)
        previous = BuildUpdate(
            sha="a" * 40, run_id=1000, run_number=10, run_attempt=1, state="building"
        )
        metadata = await service.update_build(123, previous)
        clock.now += service.settings.preview_seconds + 1
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_shutdown() -> None:
            entered.set()
            await release.wait()

        monkeypatch.setattr(service, "retry_stops", delayed_shutdown)
        cleaning = asyncio.create_task(service.cleanup_inactive())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            refreshed = await service.update_build(
                123,
                BuildUpdate(
                    sha="b" * 40, run_id=1100, run_number=11, run_attempt=1, state="building"
                ),
            )
        finally:
            release.set()
            await cleaning
        assert await service.metadata(metadata.id) == refreshed
        with pytest.raises(ArtifactConflictError, match="^stale build$"):
            (await service.update_build(123, previous))

    asyncio.run(exercise())


def test_archive_timeout_waits_for_worker_without_blocking_metadata(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        environment = await service.deploy(body(), "a" * 40, 123)
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        deadline = asyncio.Timeout(None)

        def blocked_stage(source: Path, destination: Path, limit: int) -> list[str]:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), "archive staging blocked the event loop"
            return stage_archive(source, destination, limit)

        monkeypatch.setattr(service_module, "stage_archive", blocked_stage)
        monkeypatch.setattr(service_module, "timeout", lambda _: deadline)
        deploying = asyncio.create_task(service.deploy(body(), "b" * 40, 123))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            (temporary,) = service.settings.data_dir.glob(".upload-*")
            deadline.reschedule(loop.time())
            metadata = await asyncio.wait_for(
                asyncio.create_task(service.metadata(environment.id)), timeout=0.1
            )
            await asyncio.sleep(0)
            assert metadata is not None and metadata.sha == "a" * 40
            assert deadline.expired()
            assert not deploying.done()
            assert service.uploads.locked()
            assert temporary.exists()
        finally:
            release.set()
            with pytest.raises(TimeoutError):
                await deploying
        assert (await service.store.get(environment.id)) == environment
        assert not service.uploads.locked()
        assert not list(service.settings.data_dir.glob(".upload-*"))

    asyncio.run(exercise())


def test_newer_build_event_supersedes_a_streaming_upload(
    docker_settings: Settings,
) -> None:
    async def exercise() -> None:
        service = make_service(docker_settings)
        started, release = asyncio.Event(), asyncio.Event()

        async def body() -> AsyncIterator[bytes]:
            yield binary_bundle({"echor": elf_binary()})
            started.set()
            await release.wait()

        deploying = asyncio.create_task(
            service.deploy(
                body(),
                "a" * 40,
                123,
                BuildIdentity(sha="a" * 40, run_id=1000, run_number=10, run_attempt=1),
            )
        )
        await started.wait()
        metadata = await service.update_build(
            123,
            BuildUpdate(
                sha="b" * 40,
                run_id=1100,
                run_number=11,
                run_attempt=1,
                state="building",
            ),
        )
        assert metadata.build is not None and metadata.build.sha == "b" * 40
        release.set()
        with pytest.raises(ArtifactConflictError, match="^stale build$"):
            await deploying
        assert (await service.store.get(metadata.id)) is None
        assert not list(service.settings.data_dir.glob(".upload-*"))

    asyncio.run(exercise())


def test_failed_publication_transaction_leaves_the_previous_batch_readable(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        previous = await service.deploy(body(), "a" * 40, 123)

        async def failed_publish(*args: object) -> None:
            raise OSError("database unavailable")

        monkeypatch.setattr(service.store, "_publish", failed_publish)
        with pytest.raises(OSError, match="database unavailable"):
            await service.deploy(body(), "b" * 40, 123)
        assert (await service.store.get(previous.id)) == previous
        assert previous.artifact_id in boxie(service).artifacts
        assert len(await service.sessions.uploads()) == 1
        assert not list(service.settings.data_dir.glob(".upload-*"))

    asyncio.run(exercise())


def make_service(
    settings: Settings, client: FakeBoxie | None = None, *, clock: MutableClock | None = None
) -> CliPlaygroundService:
    clock = clock or MutableClock()
    sessions = async_sessionmaker(
        create_async_engine(database_url(read_dsn(settings.postgres_dsn_file)), poolclass=NullPool),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return CliPlaygroundService(
        settings,
        EnvironmentStore(sessions, preview_seconds=settings.preview_seconds, clock=clock),
        SessionStore(sessions),
        client or FakeBoxie(clock=clock),
        clock=clock,
    )


def boxie(service: CliPlaygroundService) -> FakeBoxie:
    assert isinstance(service.boxie, FakeBoxie)
    return service.boxie


def test_failed_remote_upload_preserves_ready_and_retries_the_same_artifact(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cli_playground.boxie_client import BoxieUnavailable

    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        previous = await service.deploy(body(), "a" * 40, 123)
        remote = boxie(service)
        upload = remote.upload

        async def failed(*args: object) -> None:
            raise BoxieUnavailable()

        monkeypatch.setattr(remote, "upload", failed)
        with pytest.raises(BoxieUnavailable):
            await service.deploy(body(), "b" * 40, 123)
        assert (await service.store.get(previous.id)) == previous
        (intent,) = await service.sessions.uploads()
        monkeypatch.setattr(remote, "upload", upload)
        restarted = make_service(service.settings, remote)
        await restarted.recover()
        current = await restarted.store.get(previous.id)
        assert current is not None and current.sha == "b" * 40
        assert current.artifact_id == intent.artifact.artifact_id
        assert not (await restarted.sessions.uploads())

    asyncio.run(exercise())


def test_interrupted_product_commit_recovers_the_uploaded_identity(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        previous = await service.deploy(body(), "a" * 40, 123)
        assert isinstance(service.store, EnvironmentStore)
        original = service.store._publish

        async def failed(*args: object) -> None:
            raise OSError("interrupted commit")

        monkeypatch.setattr(service.store, "_publish", failed)
        with pytest.raises(OSError, match="interrupted"):
            await service.deploy(body(), "b" * 40, 123)
        (intent,) = await service.sessions.uploads()
        assert intent.artifact.artifact_id in boxie(service).artifacts
        assert (await service.store.get(previous.id)) == previous
        monkeypatch.setattr(service.store, "_publish", original)
        await service.recover()
        current = await service.store.get(previous.id)
        assert current is not None and current.artifact_id == intent.artifact.artifact_id
        assert len(boxie(service).artifacts) == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("close", [False, True])
def test_build_or_closure_during_remote_upload_retires_the_abandoned_artifact(
    docker_settings: Settings, monkeypatch: pytest.MonkeyPatch, close: bool
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        original = boxie(service).upload

        async def racing(identifier: UUID, digest: str, archive: Path) -> None:
            await original(identifier, digest, archive)
            if close:
                (await service.store.close_pull_request(123))
            else:
                (
                    await service.update_build(
                        123,
                        BuildUpdate(
                            sha="b" * 40,
                            run_id=1100,
                            run_number=11,
                            run_attempt=1,
                            state="building",
                        ),
                    )
                )

        monkeypatch.setattr(boxie(service), "upload", racing)
        with pytest.raises(ArtifactConflictError):
            await service.deploy(
                body(),
                "a" * 40,
                123,
                BuildIdentity(sha="a" * 40, run_id=1000, run_number=10, run_attempt=1),
            )
        assert not (await service.sessions.uploads())
        assert (await service.store.get(service.store.identifier(123))) is None
        await service.cleanup_inactive()
        assert set(boxie(service).artifacts) == boxie(service).retired

    asyncio.run(exercise())


def test_cancelled_create_recovers_same_id_and_stops_abandoned_response(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        environment = await service.deploy(body(), "a" * 40, 123)
        remote = boxie(service)
        create = remote.create
        created = asyncio.Event()

        async def ambiguous(key: str, spec: SandboxRequest) -> SandboxView:
            result = await create(key, spec)
            created.set()
            await asyncio.Event().wait()
            return result

        monkeypatch.setattr(remote, "create", ambiguous)
        task = asyncio.create_task(service.create_session(environment.id, environment.revision))
        await created.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.setattr(remote, "create", create)
        await make_service(service.settings, remote).recover()
        assert len(remote.sandboxes) == 1
        assert next(iter(remote.sandboxes.values())).state == "terminated"
        assert not (await service.sessions.creates())

    asyncio.run(exercise())


def test_abandoned_create_and_upload_retry_without_a_backend_restart(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cli_playground.boxie_client import BoxieUnavailable

    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(
            docker_settings.model_copy(update={"cleanup_interval_seconds": 0.01})
        )
        env = await service.deploy(body(), "a" * 40, 123)
        remote = boxie(service)
        original = remote.create

        async def lost(key: str, spec: SandboxRequest) -> SandboxView:
            await original(key, spec)
            raise BoxieUnavailable()

        monkeypatch.setattr(remote, "create", lost)
        with pytest.raises(BoxieUnavailable):
            await service.create_session(env.id, env.revision)
        monkeypatch.setattr(remote, "create", original)
        cleaning = asyncio.create_task(service.cleanup_periodically())
        try:
            async with asyncio.timeout(0.5):
                while next(iter(remote.sandboxes.values())).state != "terminated":
                    await asyncio.sleep(0.01)
        finally:
            cleaning.cancel()
            await asyncio.gather(cleaning, return_exceptions=True)

    asyncio.run(exercise())


def test_rerun_during_remote_admission_compensates_the_created_sandbox(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        original = await service.deploy(body(), "a" * 40, 123)
        create = boxie(service).create

        async def racing(key: str, spec: SandboxRequest) -> SandboxView:
            sandbox = await create(key, spec)
            await service.deploy(body(), "b" * 40, 123)
            return sandbox

        monkeypatch.setattr(boxie(service), "create", racing)
        with pytest.raises(ArtifactConflictError, match="revision changed"):
            await service.create_session(original.id, original.revision)
        assert len(boxie(service).sandboxes) == 1
        assert next(iter(boxie(service).sandboxes.values())).state == "terminated"

    asyncio.run(exercise())


@pytest.mark.parametrize("ci", [False, True])
@pytest.mark.parametrize("failure", ["upload", "commit"])
def test_close_reopen_never_recovers_a_previous_generation_upload(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    ci: bool,
    failure: str,
) -> None:
    from cli_playground.boxie_client import BoxieUnavailable

    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        build = (
            BuildIdentity(sha="a" * 40, run_id=1000, run_number=10, run_attempt=1) if ci else None
        )
        with monkeypatch.context() as fault:
            if failure == "upload":

                async def interrupted(*args: object) -> None:
                    raise BoxieUnavailable("interrupted upload")

                fault.setattr(boxie(service), "upload", interrupted)
                expected: type[Exception] = BoxieUnavailable
            else:

                async def failed_commit(*args: object) -> None:
                    raise OSError("interrupted commit")

                fault.setattr(service.store, "_publish", failed_commit)
                expected = OSError
            with pytest.raises(expected):
                await service.deploy(body(), "a" * 40, 123, build)
        (intent,) = await service.sessions.uploads()
        await service.close_pull_request(123)
        await service.reopen_pull_request(123)
        restarted = make_service(service.settings, boxie(service))
        await restarted.recover()
        assert (await restarted.store.get(restarted.store.identifier(123))) is None
        assert intent.artifact.artifact_id in boxie(service).retired
        assert not (await restarted.sessions.uploads())

    asyncio.run(exercise())


def test_recovery_cannot_cancel_an_attachment_claimed_after_its_snapshot(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        clock = MutableClock()
        service = make_service(docker_settings, clock=clock)
        environment = await service.deploy(body(), "a" * 40, 123)
        binding, credential = await service.create_session(environment.id, environment.revision)
        first = await service.claim_attachment(
            environment.id, binding.id, environment.revision, credential
        )
        clock.now += 41
        original = service.sessions.bindings
        replaced = False

        async def snapshot() -> list[SessionBinding]:
            nonlocal replaced
            values = await original()
            if not replaced:
                replaced = True
                await service.claim_attachment(
                    environment.id, binding.id, environment.revision, credential
                )
            return values

        monkeypatch.setattr(service.sessions, "bindings", snapshot)
        await service.recover(startup=False)
        current = await service.sessions.get(binding.id)
        assert current is not None
        assert current.attachment_claim == first.attachment_claim + 1
        assert current.attachment_deadline == clock.now + 40
        assert not current.pending_stop

    asyncio.run(exercise())


def test_durable_zip_staging_does_not_block_metadata(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        previous = await service.deploy(body(), "a" * 40, 123)
        entered, release = threading.Event(), threading.Event()
        stage = service.staging.stage

        def slow(identifier: UUID, source: Path) -> None:
            entered.set()
            assert release.wait(2), "ZIP staging blocked the event loop"
            stage(identifier, source)

        monkeypatch.setattr(service.staging, "stage", slow)
        upload = asyncio.create_task(service.deploy(body(), "b" * 40, 123))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            assert await asyncio.wait_for(service.metadata(previous.id), 1) is not None
        finally:
            release.set()
        await upload

    asyncio.run(exercise())
