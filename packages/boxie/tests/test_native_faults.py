"""Native failure injection preserves the production create-uncertainty boundary."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
import pytest
from boxie.domain.models import SandboxState, TerminationReason
from integration.faults import Faults
from integration.stack import LifecycleStack
from lifecycle_support import setup


def test_start_fault_matches_exact_route_and_restores_forwarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        forwarded: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                forwarded.append(header.split(b"\r\n", 1)[0])
                writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        with TemporaryDirectory(prefix="boxie-fault-") as socket_directory:
            socket = Path(socket_directory) / "docker.sock"
            monkeypatch.setenv("FAULT_DOCKER_SOCKET", str(socket))
            fault = Faults(tmp_path / "faults")
            stack = object.__new__(LifecycleStack)
            stack.root = tmp_path
            stack.fault("fail_start", True)
            identifier = "a" * 64
            async with (
                await asyncio.start_unix_server(upstream, path=socket),
                await asyncio.start_server(fault.docker, "127.0.0.1", 0) as proxy,
            ):
                port = proxy.sockets[0].getsockname()[1]
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", timeout=2
                ) as client:
                    route = f"/v1.47/containers/{identifier}/start"
                    response = await client.post(route)
                    assert response.status_code == 503
                    assert (fault.root / "failed_start_seen").read_text() == identifier
                    assert not forwarded
                    for method, path in (
                        ("GET", route),
                        ("POST", route + "-other"),
                        ("POST", "/v1.47/containers/create"),
                        ("DELETE", f"/v1.47/containers/{identifier}"),
                    ):
                        assert (await client.request(method, path)).status_code == 204
                    stack.fault("fail_start", False)
                    assert (await client.post(route)).status_code == 204
                    assert len(forwarded) == 5

    asyncio.run(exercise())


@pytest.mark.parametrize("boundary", ["create", "start"])
def test_injection_boundary_determines_real_orchestrator_finality(
    tmp_path: Path, boundary: str
) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path)
        known: list[str] = []

        async def fail(*args: Any, **kwargs: Any) -> None:
            if boundary == "start":
                known.append(str(args[0]))
            raise RuntimeError("owned injected failure")

        setattr(daemon, boundary, fail)
        try:
            task = agent.runtimes[request.ref.id].activity
            assert task is not None
            await asyncio.wait_for(task, 2)
            observation = await agent.inspect(request.ref)
            if boundary == "create":
                assert observation.cleanup_pending and not observation.removal_confirmed
                assert agent.records[request.ref.id].creation_uncertain
                assert not daemon.resources
            else:
                assert observation.state == SandboxState.FAILED
                assert observation.reason == TerminationReason.PROVISIONING_FAILED
                assert observation.removal_confirmed and not observation.cleanup_pending
                assert known and daemon.deleted_ids == known
                assert not daemon.resources and not daemon.started
        finally:
            await agent.close()

    asyncio.run(exercise())
