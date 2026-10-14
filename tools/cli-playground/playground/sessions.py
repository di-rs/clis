"""Async Docker terminal sessions on a dedicated sandbox host."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from aiodocker import Docker
from aiodocker.containers import DockerContainer
from aiodocker.exceptions import DockerError
from aiodocker.stream import Stream
from aiohttp import ClientError, ClientTimeout
from anyio import CancelScope

from playground.config import Settings
from playground.models import Environment

logger = logging.getLogger(__name__)


class SessionLimitError(RuntimeError):
    """The gateway's concurrent session capacity has been reached."""


@contextmanager
def docker_errors() -> Iterator[None]:
    try:
        yield
    except (DockerError, ClientError) as error:
        # Preserve useful daemon diagnostics in server logs, never in the browser.
        logger.error("Docker request failed: %.2048s", str(error))
        raise RuntimeError("Docker could not operate the sandbox; check gateway logs") from None


async def remove_container(container: DockerContainer) -> None:
    async with asyncio.timeout(20):
        while True:
            try:
                await container.delete(force=True)
                return
            except DockerError as error:
                if error.status == 404:
                    return
                if error.status != 409:
                    raise
                # AutoRemove may already be deleting after shell EOF. Confirm removal
                # before releasing the session slot or deleting its mounted binaries.
                await asyncio.sleep(0.1)


class DockerSession:
    def __init__(self, settings: Settings, environment: Environment) -> None:
        self.settings = settings
        self.environment = environment
        self.name = f"clis-playground-{uuid.uuid4().hex}"
        self.docker: Docker | None = None
        self.container: DockerContainer | None = None
        self.stream: Stream | None = None
        self.cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        with docker_errors():
            self.docker = Docker(timeout=ClientTimeout(total=20))
            async with asyncio.timeout(20):
                self.container = await self.docker.containers.create(
                    {
                        "Image": self.settings.sandbox_image,
                        "User": "65534:65534",
                        "WorkingDir": "/workspace",
                        "Env": [
                            f"PLAYGROUND_CLIS={' '.join(self.environment.clis)}",
                            "TERM=xterm-256color",
                            "HOME=/workspace",
                        ],
                        "Labels": {
                            "clis.playground": "true",
                            "clis.playground.pr": str(self.environment.pr),
                            "clis.playground.environment": self.environment.id,
                        },
                        "Tty": True,
                        "OpenStdin": True,
                        "AttachStdin": True,
                        "AttachStdout": True,
                        "AttachStderr": True,
                        "HostConfig": {
                            "AutoRemove": True,
                            "Runtime": self.settings.sandbox_runtime,
                            "LogConfig": {"Type": "none"},
                            "NetworkMode": "none",
                            "ReadonlyRootfs": True,
                            "CapDrop": ["ALL"],
                            "SecurityOpt": ["no-new-privileges"],
                            "Memory": 512 * 1024 * 1024,
                            "MemorySwap": 512 * 1024 * 1024,
                            "NanoCpus": 1_000_000_000,
                            "PidsLimit": 256,
                            "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 1024}],
                            "Tmpfs": {
                                "/workspace": "rw,nosuid,nodev,noexec,size=64m,uid=65534,gid=65534,mode=700",
                                "/tmp": "rw,nosuid,nodev,noexec,size=16m,uid=65534,gid=65534,mode=700",
                            },
                            "Mounts": [
                                {
                                    "Type": "bind",
                                    "Source": str(self.environment.binary_dir),
                                    "Target": "/opt/preview/bin",
                                    "ReadOnly": True,
                                }
                            ],
                        },
                    },
                    name=self.name,
                )
                # Attach before starting: the disabled log driver cannot replay startup output.
                self.stream = self.container.attach(stdin=True, stdout=True, stderr=True)
                await self.stream.__aenter__()
                await self.container.start()
                await self.resize(80, 24)

    async def read(self) -> bytes:
        if self.stream is None:
            return b""
        with docker_errors():
            message = await self.stream.read_out()
            return b"" if message is None else message.data

    async def write(self, data: bytes) -> None:
        if self.stream is not None:
            with docker_errors():
                await self.stream.write_in(data)

    async def resize(self, columns: int, rows: int) -> None:
        if not 1 <= columns <= 500 or not 1 <= rows <= 300:
            raise ValueError("terminal dimensions must be within 1..500 columns and 1..300 rows")
        if self.container is not None:
            with docker_errors():
                await self.container.resize(w=columns, h=rows)

    async def close(self) -> None:
        if (
            self.cleanup_task is None
            or self.cleanup_task.cancelled()
            or (self.cleanup_task.done() and self.cleanup_task.exception() is not None)
        ):
            self.cleanup_task = asyncio.create_task(self._close())
        with CancelScope(shield=True):
            await asyncio.shield(self.cleanup_task)

    async def _close(self) -> None:
        # A fresh client also permits a later retry after a failed daemon request.
        docker = self.docker or Docker(timeout=ClientTimeout(total=20))
        try:
            try:
                if self.stream is not None:
                    stream, self.stream = self.stream, None
                    await stream.close()
            finally:
                with docker_errors():
                    await remove_container(docker.containers.container(self.name))
        finally:
            await docker.close()
            self.docker = None


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.active: set[DockerSession] = set()
        self.scopes: dict[DockerSession, CancelScope] = {}
        self.finished: dict[DockerSession, asyncio.Event] = {}

    async def cleanup_orphans(self) -> None:
        """Reconcile a dedicated daemon before this single gateway starts serving."""
        with docker_errors():
            async with asyncio.timeout(60), Docker(timeout=ClientTimeout(total=20)) as docker:
                containers = await docker.containers.list(
                    all=True, filters=json.dumps({"label": ["clis.playground=true"]})
                )
                if len(containers) > 1024:
                    raise RuntimeError("too many orphan containers; operator cleanup is required")
                for container in containers:
                    await remove_container(container)

    @asynccontextmanager
    async def open(self, environment: Environment) -> AsyncIterator[DockerSession]:
        if len(self.active) >= self.settings.max_sessions:
            raise SessionLimitError("All playground sessions are busy. Try again shortly.")
        session = DockerSession(self.settings, environment)
        self.active.add(session)
        finished = asyncio.Event()
        self.finished[session] = finished
        try:
            with CancelScope() as scope:
                self.scopes[session] = scope
                async with asyncio.timeout(self.settings.session_seconds):
                    await session.start()
                    yield session
        finally:
            cleanup = asyncio.create_task(session.close())
            try:
                with CancelScope(shield=True):
                    await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                with CancelScope(shield=True):
                    await cleanup
                raise
            finally:
                if cleanup.done() and not cleanup.cancelled() and cleanup.exception() is None:
                    self.active.discard(session)
                self.scopes.pop(session, None)
                self.finished.pop(session, None)
                finished.set()

    async def close_environments(self, identifiers: set[str]) -> None:
        selected = [session for session in self.active if session.environment.id in identifiers]
        await self._stop(selected)

    async def _stop(self, selected: list[DockerSession]) -> None:
        finished = [self.finished[session] for session in selected if session in self.finished]
        for session in selected:
            if scope := self.scopes.get(session):
                scope.cancel()
        await asyncio.gather(*(event.wait() for event in finished))
        # Surface failed container removals before the gateway removes mounted binaries.
        await asyncio.gather(*(session.close() for session in selected))
        self.active.difference_update(selected)

    async def retry_cleanup(self) -> None:
        await self._stop([session for session in self.active if session not in self.scopes])

    async def close(self) -> None:
        await self._stop(list(self.active))
