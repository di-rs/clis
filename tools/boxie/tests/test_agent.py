import asyncio
from pathlib import Path

import pytest


def test_docker_adapter_confirms_auto_removal_and_bounds_daemon_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from aiodocker import Docker
    from aiodocker.containers import DockerContainer, DockerContainers
    from aiodocker.exceptions import DockerError
    from boxie.sandbox.docker import DockerBackend

    async def exercise() -> None:
        client = MagicMock(spec=Docker)
        client.containers = MagicMock(spec=DockerContainers)
        container = MagicMock(spec=DockerContainer)
        client.containers.container.return_value = container
        monkeypatch.setattr("boxie.sandbox.docker.Docker", lambda **_kwargs: client)
        backend = DockerBackend(timeout=0.15)
        container.delete.side_effect = [
            DockerError(409, "removal in progress"),
            DockerError(404, "gone"),
        ]
        await backend.delete("owned")
        assert container.delete.await_count == 2
        container.show.side_effect = DockerError(404, "gone")
        assert await backend.inspect("owned") is None
        container.delete.side_effect = DockerError(409, "still deleting")
        with pytest.raises(TimeoutError):
            await backend.delete("owned")
        await backend.close()
        assert client.close.await_count == 1

    asyncio.run(exercise())


def test_durable_provision_preserves_approved_isolation_and_entrypoint(tmp_path: Path) -> None:
    from typing import Any

    from boxie.domain.models import SandboxState
    from boxie_test_support.agent import IMAGE
    from test_agent_lifecycle import settled, setup

    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path)
        original = daemon.create
        captured: list[dict[str, Any]] = []

        async def create(name: str, config: dict[str, Any]) -> None:
            captured.append(config)
            await original(name, config)

        daemon.create = create  # type: ignore[method-assign]
        ready = await settled(agent, request.ref, SandboxState.READY)
        await agent.ensure(request)
        assert daemon.creates == 1 and ready.container_id in daemon.streams
        payload = captured[0]
        assert payload["Image"] == IMAGE
        assert payload["Cmd"] == ["/bin/sh"] and payload["Entrypoint"] == []
        assert payload["User"] == "65534:65534"
        host = payload["HostConfig"]
        assert host["Runtime"] == "runsc" and host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"]
        assert host["SecurityOpt"] == ["no-new-privileges"]
        assert host["Memory"] == host["MemorySwap"] == 536870912
        assert host["NanoCpus"] == 1000000000 and host["PidsLimit"] == 256
        assert host["Ulimits"] == [{"Name": "nofile", "Soft": 1024, "Hard": 1024}]
        assert "size=67108864" in host["Tmpfs"]["/workspace"]
        assert "size=16777216" in host["Tmpfs"]["/tmp"]
        mount = host["Mounts"][0]
        assert mount["Target"] == "/opt/artifact" and mount["ReadOnly"] is True
        assert mount["Source"] == str(tmp_path)
        await agent.close()

    asyncio.run(exercise())
