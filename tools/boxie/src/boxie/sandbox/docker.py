"""The only production adapter allowed to open the Docker socket."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aiodocker import Docker
from aiodocker.containers import DockerContainer
from aiodocker.exceptions import DockerError
from aiodocker.stream import Stream
from aiohttp import ClientTimeout

from boxie.domain.models import ProfileSnapshot


class CreateRejected(Exception):
    """The daemon definitively rejected creation before creating a container."""


class TerminalPort(Protocol):
    async def read(self) -> bytes: ...
    async def write(self, data: bytes) -> None: ...
    async def resize(self, columns: int, rows: int) -> None: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class ContainerInfo:
    id: str
    labels: dict[str, str]
    running: bool


class ExecutionDocker(Protocol):
    async def ready(self) -> bool: ...

    async def inspect(self, identifier: str) -> ContainerInfo | None: ...
    async def create(self, name: str, config: dict[str, Any]) -> None: ...
    async def attach(self, identifier: str) -> TerminalPort: ...
    async def start(self, identifier: str) -> None: ...
    async def delete(self, identifier: str) -> None: ...
    async def close(self) -> None: ...


class DockerTerminal:
    def __init__(self, container: DockerContainer, stream: Stream) -> None:
        self.container, self.stream = container, stream

    async def read(self) -> bytes:
        message = await self.stream.read_out()
        return b"" if message is None else message.data

    async def write(self, data: bytes) -> None:
        await self.stream.write_in(data)

    async def resize(self, columns: int, rows: int) -> None:
        await self.container.resize(w=columns, h=rows)

    async def close(self) -> None:
        await self.stream.close()


class DockerBackend:
    def __init__(self, timeout: float = 20) -> None:
        self.timeout = timeout
        self.client: Docker | None = None

    def _client(self) -> Docker:
        if self.client is None:
            self.client = Docker(timeout=ClientTimeout(total=self.timeout))
        return self.client

    async def ready(self) -> bool:
        await self._client().version()
        return True

    async def create(self, name: str, config: dict[str, Any]) -> None:
        try:
            await self._client().containers.create(config, name=name)
        except DockerError as error:
            if error.status in (400, 404):
                raise CreateRejected from error
            raise

    async def attach(self, name: str) -> DockerTerminal:
        container = self._client().containers.container(name)
        stream = container.attach(stdin=True, stdout=True, stderr=True)
        await stream.__aenter__()
        return DockerTerminal(container, stream)

    async def start(self, name: str) -> None:
        await self._client().containers.container(name).start()

    async def inspect(self, name: str) -> ContainerInfo | None:
        try:
            data = await self._client().containers.container(name).show()
        except DockerError as error:
            if error.status == 404:
                return None
            raise
        labels: dict[str, str] = data["Config"]["Labels"] or {}
        return ContainerInfo(data["Id"], labels, data["State"]["Running"])

    async def delete(self, name: str) -> None:
        async with asyncio.timeout(self.timeout):
            while True:
                try:
                    await self._client().containers.container(name).delete(force=True)
                    return
                except DockerError as error:
                    if error.status == 404:
                        return
                    if error.status != 409:
                        raise
                    await asyncio.sleep(0.1)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None


def container_config(
    snapshot: ProfileSnapshot, artifact: Path, labels: dict[str, str]
) -> dict[str, Any]:
    limits = snapshot.limits
    return {
        "Image": snapshot.image_digest,
        "Cmd": list(snapshot.command),
        "Entrypoint": [],
        "User": "65534:65534",
        "WorkingDir": "/workspace",
        "Env": [f"{key}={value}" for key, value in sorted(snapshot.env.items())],
        "Labels": labels,
        "Tty": True,
        "OpenStdin": True,
        "AttachStdin": True,
        "AttachStdout": True,
        "AttachStderr": True,
        "HostConfig": {
            "AutoRemove": False,
            "Runtime": "runsc",
            "LogConfig": {"Type": "none"},
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Memory": limits.memory_bytes,
            "MemorySwap": limits.memory_bytes,
            "NanoCpus": limits.cpu_nanos,
            "PidsLimit": limits.pids,
            "Ulimits": [{"Name": "nofile", "Soft": limits.nofile, "Hard": limits.nofile}],
            "Tmpfs": {
                "/workspace": f"rw,nosuid,nodev,noexec,size={limits.workspace_bytes},uid=65534,gid=65534,mode=700",
                "/tmp": f"rw,nosuid,nodev,noexec,size={limits.tmp_bytes},uid=65534,gid=65534,mode=700",
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(artifact),
                    "Target": snapshot.artifact_target,
                    "ReadOnly": True,
                }
            ],
        },
    }
