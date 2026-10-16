"""Owned fixture proxies. No product fault endpoints or shared daemon mutations.

PostgreSQL is plaintext only inside the isolated test control network. A selected
artifact COMMIT is consumed from the server but never forwarded to the agent.
The on-disk barrier is evidence of COMMIT, not of a failed INSERT.
"""

import asyncio
import contextlib
import os
import re
from pathlib import Path


class Faults:
    def __init__(
        self, root: Path, postgres_host: str = "postgres", postgres_port: int = 5432
    ) -> None:
        self.root, self.postgres_host, self.postgres_port = root, postgres_host, postgres_port
        root.mkdir(parents=True, exist_ok=True)

    def enabled(self, name: str) -> bool:
        return (self.root / name).exists()

    async def wait_release(self, name: str) -> None:
        while self.enabled(name):
            await asyncio.sleep(0.02)

    async def postgres(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream, peer = await asyncio.open_connection(self.postgres_host, self.postgres_port)
        artifact = False

        async def requests() -> None:
            nonlocal artifact
            tail = b""
            while chunk := await reader.read(65536):
                tail += chunk
                if b"INSERT INTO artifacts" in tail:
                    artifact = True
                    tail = b""
                else:
                    tail = tail[-32:]
                peer.write(chunk)
                await peer.drain()

        async def responses() -> None:
            nonlocal artifact
            while True:
                header = await upstream.readexactly(5)
                size = int.from_bytes(header[1:], "big")
                if not 4 <= size <= 16 * 1024 * 1024:
                    raise ValueError("invalid PostgreSQL frame")
                body = await upstream.readexactly(size - 4)
                if artifact and header[:1] == b"C" and body == b"COMMIT\0":
                    artifact = False
                    if self.enabled("hold_commit"):
                        (self.root / "commit_seen").write_text(
                            "server CommandComplete COMMIT consumed; response withheld\n"
                        )
                        await self.wait_release("hold_commit")
                        return  # Lost commit acknowledgment: close without forwarding COMMIT.
                writer.write(header + body)
                await writer.drain()

        await self.relay(requests(), responses(), writer, peer)

    async def docker(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer: asyncio.StreamWriter | None = None
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            first = header.split(b"\r\n", 1)[0]
            if (
                first.startswith(b"DELETE ")
                and b"/containers/" in first
                and self.enabled("fail_delete")
            ):
                (self.root / "delete_seen").touch()
                writer.write(
                    b'HTTP/1.1 503 Unavailable\r\nContent-Length: 25\r\nConnection: close\r\n\r\n{"message":"owned fault"}\n'
                )
                await writer.drain()
                return
            start = re.fullmatch(
                rb"POST (?:/v[0-9]+\.[0-9]+)?/containers/([a-f0-9]{64})/start HTTP/1\.[01]",
                first,
            )
            if start and self.enabled("fail_start"):
                # Create already returned an owned identity. Unlike an uncertain
                # create failure, this path permits physical cleanup confirmation.
                (self.root / "failed_start_seen").write_text(start[1].decode("ascii"))
                writer.write(
                    b'HTTP/1.1 503 Unavailable\r\nContent-Length: 25\r\nConnection: close\r\n\r\n{"message":"owned fault"}\n'
                )
                await writer.drain()
                return
            upstream, peer = await asyncio.open_unix_connection(
                os.environ.get("FAULT_DOCKER_SOCKET", "/var/run/docker.sock")
            )
            # Force one ordinary HTTP request per connection, so deletion cannot
            # escape classification on a reused keepalive connection. Hijacks retain headers.
            if b"upgrade:" not in header.lower():
                header = header.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n")
            peer.write(header)
            await peer.drain()

            async def requests() -> None:
                assert peer is not None
                while chunk := await reader.read(65536):
                    peer.write(chunk)
                    await peer.drain()

            async def responses() -> None:
                first_chunk = True
                while chunk := await upstream.read(65536):
                    if (
                        first_chunk
                        and b"/containers/create" in first
                        and self.enabled("hold_create")
                    ):
                        (self.root / "create_seen").touch()
                        await self.wait_release("hold_create")
                    first_chunk = False
                    writer.write(chunk)
                    await writer.drain()

            await self.relay(requests(), responses(), writer, peer)
        except OSError, asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            writer.close()
            if peer:
                peer.close()

    @staticmethod
    async def relay(
        first: object, second: object, writer: asyncio.StreamWriter, peer: asyncio.StreamWriter
    ) -> None:
        # Coroutine annotations stay precise for strict root mypy.
        from collections.abc import Coroutine
        from typing import Any, cast

        tasks = [
            asyncio.create_task(cast(Coroutine[Any, Any, None], item)) for item in (first, second)
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            peer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
                await peer.wait_closed()


async def main() -> None:
    root = Path(os.environ.get("FAULT_ROOT", "/faults"))
    faults = Faults(root)
    socket = root / "docker.sock"
    socket.unlink(missing_ok=True)
    docker = await asyncio.start_unix_server(faults.docker, str(socket))
    socket.chmod(0o666)
    postgres = await asyncio.start_server(faults.postgres, "0.0.0.0", 5432)
    async with docker, postgres:
        await asyncio.gather(docker.serve_forever(), postgres.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
