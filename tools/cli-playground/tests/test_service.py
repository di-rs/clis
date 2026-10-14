"""Service lifecycle boundaries independent of HTTP validation."""

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from playground import service as service_module
from playground.artifacts import stage_archive
from playground.config import Settings
from playground.service import PlaygroundService

from .helpers import binary_bundle, elf_binary


@pytest.mark.usefixtures("configured_environment")
def test_archive_timeout_waits_for_worker_without_blocking_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = PlaygroundService(Settings())
        environment = await service.deploy(body(), "a" * 40, 123)
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        deadline = asyncio.Timeout(None)

        def blocked_stage(source: Path, destination: Path, limit: int) -> list[str]:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), "archive staging blocked the event loop"
            return stage_archive(source, destination, limit)

        monkeypatch.setattr(service_module, "stage_archive", blocked_stage)
        monkeypatch.setattr(asyncio, "timeout", lambda _: deadline)
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


@pytest.mark.usefixtures("configured_environment")
def test_replacement_blocks_new_sessions_and_keeps_old_files_until_cleanup_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body(payload: bytes = b"old") -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary(payload)})

    async def exercise() -> None:
        service = PlaygroundService(Settings())
        previous = await service.deploy(body(), "a" * 40, 123)
        entered, release = asyncio.Event(), asyncio.Event()

        async def close_environments(identifiers: set[str]) -> None:
            if previous.id in identifiers:
                entered.set()
                await release.wait()
                assert (previous.binary_dir / "echor").read_bytes() == elf_binary(b"old")

        monkeypatch.setattr(service.sessions, "close_environments", close_environments)
        deploying = asyncio.create_task(service.deploy(body(b"new"), "b" * 40, 123))
        await entered.wait()
        try:
            assert previous.id in getattr(service, "updating", set())
            metadata = await service.metadata(previous.id)
            assert metadata is not None and metadata.sha == "a" * 40
        finally:
            release.set()
            current = await deploying
        assert current.id == previous.id
        assert current.sha == "b" * 40
        assert previous.id not in service.updating
        assert not previous.binary_dir.exists()

    asyncio.run(exercise())


@pytest.mark.usefixtures("configured_environment")
def test_failed_session_cleanup_preserves_the_current_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = PlaygroundService(Settings())
        previous = await service.deploy(body(), "a" * 40, 123)

        async def failed_cleanup(identifiers: set[str]) -> None:
            if previous.id in identifiers:
                raise RuntimeError("Docker unavailable")

        monkeypatch.setattr(service.sessions, "close_environments", failed_cleanup)
        with pytest.raises(RuntimeError, match="Docker unavailable"):
            await service.deploy(body(), "b" * 40, 123)
        assert service.store.get(previous.id) == previous
        assert not list(service.settings.data_dir.glob(".upload-*"))
        assert previous.id not in getattr(service, "updating", set())

    asyncio.run(exercise())


@pytest.mark.usefixtures("configured_environment")
def test_failed_manifest_swap_leaves_the_previous_batch_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = PlaygroundService(Settings())
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
        assert (previous.binary_dir / "echor").read_bytes() == elf_binary()
        assert not (previous.binary_dir.parent / ("b" * 40)).exists()
        assert not list(service.settings.data_dir.glob(".upload-*"))
        assert previous.id not in service.updating

    asyncio.run(exercise())


@pytest.mark.usefixtures("configured_environment")
def test_updating_preview_rejects_new_websockets_before_starting_a_sandbox() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from playground.routes import create_router
    from starlette.websockets import WebSocketDisconnect

    service = PlaygroundService(Settings())
    temporary = service.store.temporary_directory()
    (temporary / "echor").write_bytes(elf_binary())
    environment = service.store.publish(temporary, ["echor"], "a" * 40, 123)
    service.updating.add(environment.id)
    app = FastAPI()
    app.include_router(create_router(service, service.settings.data_dir))
    with TestClient(app, base_url=service.settings.public_url) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(
                f"wss://play.example/ws/{environment.id}?sha={'a' * 40}",
                headers={"Origin": service.settings.public_url},
            ):
                pytest.fail("updating revision opened a terminal")
        assert error.value.code == 1013
    assert not service.sessions.active
