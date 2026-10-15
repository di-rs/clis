"""Exercise session policy and terminal transport at the Docker client boundary."""

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiodocker import Docker
from aiodocker.containers import DockerContainer, DockerContainers
from aiodocker.exceptions import DockerError
from aiodocker.stream import Message, Stream
from fastapi import WebSocket
from fastapi.testclient import TestClient
from playground.app import create_app
from playground.config import Settings
from playground.models import Environment
from playground.sessions import SessionLimitError, SessionManager
from starlette.websockets import WebSocketDisconnect

from .helpers import upload
from .test_api import build_event, build_upload


def test_same_sha_publication_preserves_pinned_session_and_collects_after_disconnect(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
) -> None:
    from .helpers import elf_binary

    app = create_app(docker_settings)
    with TestClient(app, base_url=docker_settings.public_url) as client:
        deployed = build_upload(client)
        assert deployed.status_code == 201
        identifier = deployed.json()["id"]
        metadata = client.get(f"/api/environments/{identifier}").json()
        old_dir = docker_settings.data_dir / identifier / metadata["revision"]
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}&revision={metadata['revision']}",
            headers={"Origin": docker_settings.public_url},
        ) as websocket:
            assert websocket.receive_bytes() == b"ready\n"
            assert build_upload(client, attempt=2, payload=b"new").status_code == 201
            assert (old_dir / "echor").read_bytes() == elf_binary()
            websocket.send_json({"type": "input", "data": "still alive"})
            assert websocket.receive_bytes() == b"still alive"
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(
                    f"wss://play.example/ws/{identifier}?sha={'a' * 40}&revision={metadata['revision']}",
                    headers={"Origin": docker_settings.public_url},
                ):
                    pytest.fail("a superseded build admitted a new session")
            assert not docker_daemon.removed
        # The WebSocket context waits for the handler's cleanup to finish.
        assert not old_dir.exists()
        current = client.get(f"/api/environments/{identifier}").json()
        assert (docker_settings.data_dir / identifier / current["revision"] / "echor").is_file()


def test_empty_build_stops_new_admission_without_stopping_existing_shell(
    docker_settings: Settings,
) -> None:
    with TestClient(create_app(docker_settings), base_url=docker_settings.public_url) as client:
        deployed = build_upload(client)
        assert deployed.status_code == 201
        identifier = deployed.json()["id"]
        metadata = client.get(f"/api/environments/{identifier}").json()
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}&revision={metadata['revision']}",
            headers={"Origin": docker_settings.public_url},
        ) as websocket:
            assert websocket.receive_bytes() == b"ready\n"
            assert build_event(client, "empty", run=11).status_code == 200
            websocket.send_json({"type": "input", "data": "retained"})
            assert websocket.receive_bytes() == b"retained"
            assert client.get(f"/api/environments/{identifier}").json()["sha"] is None


def test_revision_is_rechecked_after_accept_even_when_source_sha_is_unchanged(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from playground.models import BuildIdentity

    from .helpers import elf_binary

    app = create_app(docker_settings)
    original_accept = WebSocket.accept

    async def accept_then_replace(websocket: WebSocket) -> None:
        await original_accept(websocket)
        staged = app.state.store.temporary_directory()
        (staged / "echor").write_bytes(elf_binary(b"new"))
        app.state.store.publish(
            staged,
            ["echor"],
            "a" * 40,
            123,
            BuildIdentity(sha="a" * 40, run_id=1000, run_number=10, run_attempt=2),
        )

    with TestClient(app, base_url=docker_settings.public_url) as client:
        deployed = build_upload(client)
        assert deployed.status_code == 201
        identifier = deployed.json()["id"]
        metadata = client.get(f"/api/environments/{identifier}").json()
        monkeypatch.setattr(WebSocket, "accept", accept_then_replace)
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}&revision={metadata['revision']}",
            headers={"Origin": docker_settings.public_url},
        ) as websocket:
            assert websocket.receive() == {"type": "websocket.close", "code": 1008, "reason": ""}
    assert not docker_daemon.configs


def test_ci_revision_cannot_be_opened_without_its_artifact_version(
    docker_settings: Settings,
) -> None:
    with TestClient(create_app(docker_settings), base_url=docker_settings.public_url) as client:
        deployed = build_upload(client)
        assert deployed.status_code == 201
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"wss://play.example/ws/{deployed.json()['id']}?sha={'a' * 40}",
                headers={"Origin": docker_settings.public_url},
            ):
                pytest.fail("a SHA-only connection silently selected a CI build attempt")


def test_failed_cleanup_retains_superseded_artifact_until_container_is_removed(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
) -> None:
    from collections.abc import AsyncIterator

    from playground.service import PlaygroundService

    from .helpers import binary_bundle, elf_binary

    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = PlaygroundService(docker_settings)
        original = await service.deploy(body(), "a" * 40, 123)
        with pytest.raises(RuntimeError, match="Docker could not"):
            async with service.sessions.open(original) as session:
                assert await session.read() == b"ready\n"
                await service.deploy(body(), "b" * 40, 123)
                docker_daemon.failures["delete"] = DockerError(500, "unavailable")
        service.collect_artifacts()
        assert original.binary_dir.exists()
        docker_daemon.failures.clear()
        await service.cleanup_inactive()
        assert not original.binary_dir.exists()
        assert not service.sessions.active

    asyncio.run(exercise())


def test_failed_manual_republication_does_not_remove_a_retained_revision(
    docker_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncIterator

    from playground.service import PlaygroundService

    from .helpers import binary_bundle, elf_binary

    async def body() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        service = PlaygroundService(docker_settings)
        original = await service.deploy(body(), "a" * 40, 123)
        async with service.sessions.open(original) as session:
            assert await session.read() == b"ready\n"
            await service.deploy(body(), "b" * 40, 123)
            replace = Path.replace

            def failed_swap(path: Path, target: str | Path) -> Path:
                if path.name == ".metadata-upload":
                    raise OSError("disk unavailable")
                return replace(path, target)

            monkeypatch.setattr(Path, "replace", failed_swap)
            with pytest.raises(OSError, match="disk unavailable"):
                await service.deploy(body(), "a" * 40, 123)
            assert (original.binary_dir / "echor").read_bytes() == elf_binary()

    asyncio.run(exercise())


class DockerDaemon:
    """Only the external Docker boundary is replaced; session/lifecycle code runs normally."""

    def __init__(self) -> None:
        self.clients: list[MagicMock] = []
        self.containers: dict[str, MagicMock] = {}
        self.configs: list[dict[str, object]] = []
        self.removed: list[str] = []
        self.failures: dict[str, Exception] = {}
        self.listed: list[MagicMock] = []
        self.list_requests: list[dict[str, object]] = []
        self.expected_binary: Path | None = None

    def client(self, **_kwargs: object) -> MagicMock:
        client = MagicMock(spec=Docker)
        client.__aenter__.return_value = client

        async def exit_client(*_args: object) -> None:
            await client.close()

        client.__aexit__.side_effect = exit_client
        client.containers = MagicMock(spec=DockerContainers)
        client.containers.create.side_effect = self.create
        client.containers.container.side_effect = self.container
        client.containers.list.side_effect = self.list_containers
        self.clients.append(client)
        return client

    def container(self, name: str) -> MagicMock:
        if name not in self.containers:
            container = MagicMock(spec=DockerContainer)
            container.id = name

            async def delete(*, force: bool) -> None:
                await self.delete(name, force=force)

            container.delete.side_effect = delete
            self.containers[name] = container
        return self.containers[name]

    async def create(self, config: dict[str, object], *, name: str) -> MagicMock:
        self.configs.append(config)
        container = self.container(name)
        queue: asyncio.Queue[Message | None] = asyncio.Queue()
        stream = MagicMock(spec=Stream)
        stream.__aenter__.return_value = stream
        stream.read_out.side_effect = queue.get
        stream.write_in.side_effect = lambda data: queue.put_nowait(Message(1, data))
        container.attach.return_value = stream

        async def start() -> None:
            # Starting before attaching loses output because session logs are disabled.
            assert stream.__aenter__.await_count == 1
            if failure := self.failures.get("start"):
                raise failure
            queue.put_nowait(Message(1, b"ready\n"))

        container.start.side_effect = start
        return container

    async def delete(self, name: str, *, force: bool) -> None:
        assert force
        if self.expected_binary is not None:
            assert self.expected_binary.is_file()
        if failure := self.failures.get("delete"):
            raise failure
        self.removed.append(name)

    async def list_containers(self, **kwargs: object) -> list[MagicMock]:
        self.list_requests.append(kwargs)
        return self.listed


@pytest.fixture(autouse=True)
def docker_daemon(monkeypatch: pytest.MonkeyPatch) -> DockerDaemon:
    daemon = DockerDaemon()
    monkeypatch.setattr("playground.sessions.Docker", daemon.client)
    return daemon


def environment(settings: Settings) -> Environment:
    directory = settings.data_dir / ("a" * 32) / ("b" * 40)
    directory.mkdir(parents=True)
    (directory / "echor").write_bytes(b"test binary")
    (directory / "catr").write_bytes(b"another test binary")
    return Environment(
        id="a" * 32,
        clis=["catr", "echor"],
        sha="b" * 40,
        pr=123,
        expires_at=10**12,
        binary_dir=directory,
    )


def test_session_roundtrip_limits_and_cleanup(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
) -> None:
    settings = docker_settings
    preview = environment(settings)

    async def exercise() -> None:
        manager = SessionManager(settings)
        async with manager.open(preview) as session:
            assert await session.read() == b"ready\n"
            await session.write(b"hello\n")
            assert await session.read() == b"hello\n"
            await session.resize(100, 30)
            docker_daemon.containers[session.name].resize.assert_awaited_with(w=100, h=30)
        assert not manager.active

    asyncio.run(exercise())
    config = docker_daemon.configs[0]
    assert config["User"] == "65534:65534"
    host = config["HostConfig"]
    assert isinstance(host, dict)
    assert host["Runtime"] == "runsc"
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert host["SecurityOpt"] == ["no-new-privileges"]
    assert host["Memory"] > 0 and host["PidsLimit"] > 0
    assert host["Mounts"] == [
        {
            "Type": "bind",
            "Source": str(preview.binary_dir),
            "Target": "/opt/preview/bin",
            "ReadOnly": True,
        }
    ]
    assert len(docker_daemon.removed) == 1
    assert all(client.close.await_count == 1 for client in docker_daemon.clients)


def test_capacity_released_after_disconnect(
    docker_settings: Settings, docker_daemon: DockerDaemon
) -> None:
    async def exercise() -> None:
        manager = SessionManager(docker_settings)
        preview = environment(docker_settings)
        async with manager.open(preview):
            with pytest.raises(SessionLimitError):
                async with manager.open(preview):
                    pytest.fail("over-capacity session admitted")
        async with manager.open(preview) as session:
            assert await session.read() == b"ready\n"

    asyncio.run(exercise())
    assert len(docker_daemon.removed) == 2


def test_silent_session_timeout_forces_container_removal(
    docker_settings: Settings, docker_daemon: DockerDaemon
) -> None:
    async def exercise() -> None:
        manager = SessionManager(
            Settings(**{**docker_settings.model_dump(), "session_seconds": 0.2})
        )
        with pytest.raises(TimeoutError):
            async with manager.open(environment(docker_settings)):
                await asyncio.Event().wait()

    asyncio.run(exercise())
    assert docker_daemon.removed


def test_websocket_disconnect_removes_live_container(
    docker_settings: Settings, docker_daemon: DockerDaemon
) -> None:
    docker_settings = Settings(**{**docker_settings.model_dump(), "preview_domain": "play.example"})
    with TestClient(create_app(docker_settings), base_url=docker_settings.public_url) as client:
        identifier = upload(client)["id"]
        authority = f"{identifier}.play.example"
        with client.websocket_connect(
            f"wss://{authority}/ws/{identifier}?sha={'a' * 40}",
            headers={"Origin": f"https://{authority}"},
        ) as websocket:
            assert websocket.receive_bytes() == b"ready\n"
            websocket.send_json({"type": "input", "data": "hello\n"})
            assert websocket.receive_bytes() == b"hello\n"
    assert docker_daemon.removed


def test_failed_start_cleans_up_and_logs_bounded_diagnostics(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
    caplog: pytest.LogCaptureFixture,
) -> None:
    docker_daemon.failures["start"] = DockerError(500, "missing sandbox runtime " + "x" * 4096)

    async def exercise() -> None:
        manager = SessionManager(docker_settings)
        with pytest.raises(RuntimeError, match="check gateway logs"):
            async with manager.open(environment(docker_settings)):
                pytest.fail("failed Docker request opened a session")
        assert not manager.active

    asyncio.run(exercise())
    assert len(docker_daemon.removed) == 1
    assert "missing sandbox runtime" in caplog.text
    assert "x" * 2049 not in caplog.text
    assert all(client.close.await_count == 1 for client in docker_daemon.clients)


def test_terminal_output_cap_counts_bytes_and_removes_container(
    docker_settings: Settings, docker_daemon: DockerDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("playground.routes.MAX_TERMINAL_OUTPUT_BYTES", 10, raising=False)
    with TestClient(create_app(docker_settings), base_url=docker_settings.public_url) as client:
        identifier = upload(client)["id"]
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}",
            headers={"Origin": "https://play.example"},
        ) as websocket:
            assert websocket.receive_bytes() == b"ready\n"
            websocket.send_json({"type": "input", "data": "ééé"})
            assert websocket.receive_bytes() == "éé".encode()
            status = websocket.receive_json()
            assert status == {
                "type": "status",
                "message": "Terminal output limit reached. Redirect large output to a file.",
            }
            assert websocket.receive()["type"] == "websocket.close"
    assert docker_daemon.removed


def test_invalid_terminal_size_closes_and_removes_session(
    docker_settings: Settings, docker_daemon: DockerDaemon
) -> None:
    with TestClient(create_app(docker_settings), base_url=docker_settings.public_url) as client:
        preview = upload(client)
        with client.websocket_connect(
            f"wss://play.example/ws/{preview['id']}?sha={'a' * 40}",
            headers={"Origin": docker_settings.public_url},
        ) as websocket:
            assert websocket.receive_bytes() == b"ready\n"
            websocket.send_json({"type": "resize", "cols": 0, "rows": 24})
            assert websocket.receive_json() == {
                "type": "status",
                "message": "Invalid terminal input.",
            }
            assert websocket.receive() == {"type": "websocket.close", "code": 1008, "reason": ""}
    assert docker_daemon.removed


def test_expiration_closes_active_terminal_without_new_requests(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
) -> None:
    settings = Settings(
        **{**docker_settings.model_dump(), "preview_seconds": 0.2, "cleanup_interval_seconds": 0.02}
    )
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        identifier = upload(client)["id"]
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}",
            headers={"Origin": "https://play.example"},
        ) as websocket:
            assert websocket.receive_bytes() == b"ready\n"
            for _ in range(100):
                if not (settings.data_dir / identifier).exists():
                    break
                time.sleep(0.02)
            assert not (settings.data_dir / identifier).exists()
            assert docker_daemon.removed


def test_close_pr_stops_active_terminal_before_removing_binary(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
) -> None:
    app = create_app(docker_settings)
    with TestClient(app, base_url=docker_settings.public_url) as client:
        identifier = upload(client)["id"]
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}",
            headers={"Origin": "https://play.example"},
        ) as websocket:
            assert websocket.receive_bytes() == b"ready\n"
            preview = app.state.store.get(identifier)
            docker_daemon.expected_binary = preview.binary_dir / "echor"
            closed = client.delete(
                "/api/pull-requests/123", headers={"Authorization": "Bearer test-deployment-token"}
            )
            assert closed.json() == {"deleted": 1}
            assert docker_daemon.removed
            assert not (docker_settings.data_dir / identifier).exists()


def test_failed_container_removal_stays_pending_until_retry_succeeds(
    docker_settings: Settings, docker_daemon: DockerDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        manager = SessionManager(docker_settings)
        preview = environment(docker_settings)
        docker_daemon.failures["delete"] = DockerError(500, "removal failed")
        with pytest.raises(RuntimeError):
            async with manager.open(preview) as session:
                assert await session.read() == b"ready\n"
        with pytest.raises(RuntimeError):
            await manager.close_environments({preview.id})
        del docker_daemon.failures["delete"]
        await manager.retry_cleanup()
        assert not manager.active

    asyncio.run(exercise())
    assert len(docker_daemon.removed) == 1


def test_closure_during_websocket_handshake_cannot_start_a_container(
    docker_settings: Settings, docker_daemon: DockerDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(docker_settings)
    original_accept = WebSocket.accept

    async def accept_then_close(websocket: WebSocket) -> None:
        await original_accept(websocket)
        app.state.store.close_pull_request(123)

    monkeypatch.setattr(WebSocket, "accept", accept_then_close)
    with TestClient(app, base_url=docker_settings.public_url) as client:
        identifier = upload(client)["id"]
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}",
            headers={"Origin": "https://play.example"},
        ) as websocket:
            assert websocket.receive()["type"] == "websocket.close"
    assert not docker_daemon.configs


def test_external_cancellation_waits_for_removal(
    docker_settings: Settings, docker_daemon: DockerDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        manager = SessionManager(docker_settings)
        ready, deleting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def delayed_delete(name: str, *, force: bool) -> None:
            deleting.set()
            await release.wait()
            docker_daemon.removed.append(name)

        monkeypatch.setattr(docker_daemon, "delete", delayed_delete)

        async def live() -> None:
            async with manager.open(environment(docker_settings)):
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(live())
        await ready.wait()
        task.cancel()
        await deleting.wait()
        assert manager.active
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not manager.active
        assert docker_daemon.removed

    asyncio.run(exercise())


def test_orphan_cleanup_selects_service_label(
    docker_settings: Settings, docker_daemon: DockerDaemon
) -> None:
    docker_daemon.listed = [docker_daemon.container("a" * 64), docker_daemon.container("c" * 64)]
    asyncio.run(SessionManager(docker_settings).cleanup_orphans())
    assert docker_daemon.list_requests == [
        {"all": True, "filters": '{"label": ["clis.playground=true"]}'}
    ]
    assert docker_daemon.removed == ["a" * 64, "c" * 64]


def test_startup_refuses_requests_when_orphan_cleanup_fails(
    docker_settings: Settings,
    docker_daemon: DockerDaemon,
) -> None:
    docker_daemon.listed = [docker_daemon.container("a" * 64)]
    docker_daemon.failures["delete"] = DockerError(500, "unavailable")
    settings = Settings(**{**docker_settings.model_dump(), "reconcile_sessions_on_start": True})
    with pytest.raises(RuntimeError):
        with TestClient(create_app(settings), base_url=settings.public_url):
            pytest.fail("gateway accepted requests before successful reconciliation")


def test_container_auto_removal_race_releases_capacity(
    docker_settings: Settings, docker_daemon: DockerDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    async def auto_removing(name: str, *, force: bool) -> None:
        assert force
        attempts.append(name)
        if len(attempts) < 3:
            raise DockerError(409, "removal of container is already in progress")
        raise DockerError(404, "No such container")

    monkeypatch.setattr(docker_daemon, "delete", auto_removing)

    async def exercise() -> None:
        manager = SessionManager(docker_settings)
        async with manager.open(environment(docker_settings)):
            pass
        assert not manager.active

    asyncio.run(exercise())
    assert len(attempts) == 3
    assert len(set(attempts)) == 1
    assert all(client.close.await_count == 1 for client in docker_daemon.clients)


def test_socket_requires_the_revision_loaded_by_the_page(
    docker_settings: Settings, docker_daemon: DockerDaemon
) -> None:
    with TestClient(create_app(docker_settings), base_url=docker_settings.public_url) as client:
        identifier = upload(client)["id"]
        upload(client, sha="b" * 40)
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(
                f"wss://play.example/ws/{identifier}?sha={'a' * 40}",
                headers={"Origin": docker_settings.public_url},
            ):
                pytest.fail("stale metadata opened a newer binary revision")
        assert error.value.code == 1008
    assert not docker_daemon.configs


def test_revision_replacement_during_accept_cannot_start_a_mislabeled_terminal(
    docker_settings: Settings, docker_daemon: DockerDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .helpers import elf_binary

    app = create_app(docker_settings)
    original_accept = WebSocket.accept

    async def accept_then_replace(websocket: WebSocket) -> None:
        await original_accept(websocket)
        temporary = app.state.store.temporary_directory()
        (temporary / "echor").write_bytes(elf_binary())
        app.state.store.publish(temporary, ["echor"], "b" * 40, 123)

    monkeypatch.setattr(WebSocket, "accept", accept_then_replace)
    with TestClient(app, base_url=docker_settings.public_url) as client:
        identifier = upload(client)["id"]
        with client.websocket_connect(
            f"wss://play.example/ws/{identifier}?sha={'a' * 40}",
            headers={"Origin": docker_settings.public_url},
        ) as websocket:
            assert websocket.receive() == {"type": "websocket.close", "code": 1008, "reason": ""}
    assert not docker_daemon.configs
