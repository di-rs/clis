"""Service lifecycle boundaries independent of HTTP validation."""

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from playground import service as service_module
from playground.artifacts import stage_archive
from playground.boxie_client import SandboxRequest, SandboxView
from playground.config import Settings
from playground.models import BuildIdentity, BuildUpdate
from playground.service import PlaygroundService
from playground.session_store import SessionStore
from playground.storage import ArtifactConflictError, EnvironmentStore

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
        metadata = service.update_build(123, previous)
        clock.now += service.settings.preview_seconds + 1
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_shutdown() -> None:
            entered.set()
            await release.wait()

        monkeypatch.setattr(service, "retry_stops", delayed_shutdown)
        cleaning = asyncio.create_task(service.cleanup_inactive())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            refreshed = service.update_build(
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
            service.update_build(123, previous)

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
        assert service.store.get(environment.id) == environment
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
        metadata = service.update_build(
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
        assert service.store.get(metadata.id) is None
        assert not list(service.settings.data_dir.glob(".upload-*"))

    asyncio.run(exercise())


def test_failed_manifest_swap_leaves_the_previous_batch_readable(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = make_service(docker_settings)
        previous = await service.deploy(body(), "a" * 40, 123)
        replace = Path.replace

        def failed_swap(path: Path, target: str | Path) -> Path:
            if path.name == ".metadata-upload":
                raise OSError("disk unavailable")
            return replace(path, target)

        monkeypatch.setattr(Path, "replace", failed_swap)
        with pytest.raises(OSError, match="disk unavailable"):
            await service.deploy(body(), "b" * 40, 123)
        assert service.store.get(previous.id) == previous
        assert previous.artifact_id in boxie(service).artifacts
        assert len(service.sessions.uploads()) == 1
        assert not list(service.settings.data_dir.glob(".upload-*"))

    asyncio.run(exercise())


def make_service(
    settings: Settings, client: FakeBoxie | None = None, *, clock: MutableClock | None = None
) -> PlaygroundService:
    clock = clock or MutableClock()
    return PlaygroundService(
        settings,
        EnvironmentStore(settings, clock=clock),
        SessionStore(settings.data_dir),
        client or FakeBoxie(clock=clock),
        clock=clock,
    )


def boxie(service: PlaygroundService) -> FakeBoxie:
    assert isinstance(service.boxie, FakeBoxie)
    return service.boxie


def test_failed_remote_upload_preserves_ready_and_retries_the_same_artifact(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from playground.boxie_client import BoxieUnavailable

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
        assert service.store.get(previous.id) == previous
        (intent,) = service.sessions.uploads()
        monkeypatch.setattr(remote, "upload", upload)
        restarted = make_service(service.settings, remote)
        await restarted.recover()
        current = restarted.store.get(previous.id)
        assert current is not None and current.sha == "b" * 40
        assert current.artifact_id == intent.artifact.artifact_id
        assert not restarted.sessions.uploads()

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
        original = service.store._write_record

        def failed(*args: object) -> None:
            raise OSError("interrupted commit")

        monkeypatch.setattr(service.store, "_write_record", failed)
        with pytest.raises(OSError, match="interrupted"):
            await service.deploy(body(), "b" * 40, 123)
        (intent,) = service.sessions.uploads()
        assert intent.artifact.artifact_id in boxie(service).artifacts
        assert service.store.get(previous.id) == previous
        monkeypatch.setattr(service.store, "_write_record", original)
        await service.recover()
        current = service.store.get(previous.id)
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
                service.store.close_pull_request(123)
            else:
                service.update_build(
                    123,
                    BuildUpdate(
                        sha="b" * 40, run_id=1100, run_number=11, run_attempt=1, state="building"
                    ),
                )

        monkeypatch.setattr(boxie(service), "upload", racing)
        with pytest.raises(ArtifactConflictError):
            await service.deploy(
                body(),
                "a" * 40,
                123,
                BuildIdentity(sha="a" * 40, run_id=1000, run_number=10, run_attempt=1),
            )
        assert not service.sessions.uploads()
        assert service.store.get(service.store.identifier(123)) is None
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
        assert not service.sessions.creates()

    asyncio.run(exercise())


def test_abandoned_create_and_upload_retry_without_a_backend_restart(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from playground.boxie_client import BoxieUnavailable

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
    from playground.boxie_client import BoxieUnavailable

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

                def failed_commit(*args: object) -> None:
                    raise OSError("interrupted commit")

                fault.setattr(service.store, "_write_record", failed_commit)
                expected = OSError
            with pytest.raises(expected):
                await service.deploy(body(), "a" * 40, 123, build)
        (intent,) = service.sessions.uploads()
        await service.close_pull_request(123)
        await service.reopen_pull_request(123)
        restarted = make_service(service.settings, boxie(service))
        await restarted.recover()
        assert restarted.store.get(restarted.store.identifier(123)) is None
        assert intent.artifact.artifact_id in boxie(service).retired
        assert not restarted.sessions.uploads()

    asyncio.run(exercise())
