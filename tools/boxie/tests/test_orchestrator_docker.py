"""Docker response classification and bounded cleanup."""

import asyncio

import pytest
from pydantic import ValidationError


def test_docker_adapter_confirms_auto_removal_and_bounds_daemon_wait() -> None:
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
        backend = DockerBackend(timeout=0.15, client=client)
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


def test_docker_inspect_rejects_malformed_fields() -> None:
    from unittest.mock import MagicMock

    from aiodocker import Docker
    from aiodocker.containers import DockerContainer, DockerContainers
    from boxie.sandbox.docker import DockerBackend

    async def exercise() -> None:
        client = MagicMock(spec=Docker)
        client.containers = MagicMock(spec=DockerContainers)
        container = MagicMock(spec=DockerContainer)
        client.containers.container.return_value = container
        container.show.return_value = {
            "Id": "owned",
            "Config": {"Labels": {"owner": 7}},
            "State": {"Running": "yes"},
        }
        with pytest.raises(ValidationError):
            await DockerBackend(client=client).inspect("owned")

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [400, 404, 409, 500])
def test_only_definitive_docker_rejections_prove_no_creation(status: int) -> None:
    from unittest.mock import MagicMock

    from aiodocker import Docker
    from aiodocker.containers import DockerContainers
    from aiodocker.exceptions import DockerError
    from boxie.sandbox.docker import CreateRejected, DockerBackend

    async def exercise() -> None:
        client = MagicMock(spec=Docker)
        client.containers = MagicMock(spec=DockerContainers)
        client.containers.create.side_effect = DockerError(status, "create failed")
        backend = DockerBackend(client=client)
        with pytest.raises(CreateRejected if status in (400, 404) else DockerError):
            await backend.create("owned", {})
        await backend.close()

    asyncio.run(exercise())
