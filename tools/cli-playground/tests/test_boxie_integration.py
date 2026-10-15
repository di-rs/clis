"""Product admission contract, exercised through the public HTTP boundary."""

import time
from pathlib import Path

import aiohttp
import pytest
from fastapi.testclient import TestClient

from .helpers import upload


def test_page_admission_returns_a_private_credential_and_exact_revision(
    api_client: TestClient,
) -> None:
    deployed = upload(api_client)
    response = api_client.post(
        f"/api/environments/{deployed['id']}/sessions",
        headers={"Origin": "https://play.example"},
        json={"revision": "a" * 40},
    )
    assert response.status_code == 201, response.text
    binding = response.json()
    assert binding["revision"] == "a" * 40
    assert len(binding["credential"]) >= 43
    assert binding["absolute_deadline"] and binding["id"]


def test_separate_product_api_agent_publish_terminal_reset_and_pr_close(tmp_path: Path) -> None:
    import asyncio
    import json
    import socket
    import sys
    from datetime import UTC, datetime
    from uuid import UUID

    import httpx
    import uvicorn
    from playground.app import create_app
    from playground.boxie_client import HttpBoxieClient
    from playground.config import Settings
    from websockets.asyncio.client import connect
    from websockets.typing import Origin

    # Reuse Boxie's daemon-boundary fixture; every application hop uses loopback HTTP/WS.
    sys.path.insert(0, str(Path(__file__).parents[2] / "boxie" / "tests"))
    from api_test_support import api_server
    from boxie_test_support import Clock, DockerDaemon, configuration, live

    from .helpers import binary_bundle, elf_binary

    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path / "agent"), DockerDaemon(), Clock()
        clock.now = datetime.now(UTC)
        profiles = json.loads(config.profiles_file.read_text())
        profiles["shell"]["commands"] = [["/opt/playground/shell.sh"]]
        config.profiles_file.write_text(json.dumps(profiles))
        async with live(config, daemon, clock) as (agent_http, agent):
            async with api_server(str(agent_http.base_url)) as api:
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                url = f"http://127.0.0.1:{sock.getsockname()[1]}"
                settings = Settings(
                    data_dir=tmp_path / "product",
                    public_url=url,
                    deploy_token="test-deployment-token",
                    boxie_url=str(api.base_url),
                    boxie_profile="shell",
                    boxie_credential_file=tmp_path / "secret",
                )
                app = create_app(
                    settings, boxie=HttpBoxieClient(str(api.base_url), "consumer-one-secret")
                )
                server = uvicorn.Server(
                    uvicorn.Config(app, log_level="error", ws="websockets-sansio", access_log=False)
                )
                task = asyncio.create_task(server.serve(sockets=[sock]))
                try:
                    for _ in range(200):
                        if server.started:
                            break
                        await asyncio.sleep(0.01)
                    assert server.started
                    async with httpx.AsyncClient(base_url=url) as client:

                        async def publish(sha: str, pr: int = 123) -> str:
                            response = await client.post(
                                "/api/environments",
                                params={"sha": sha, "pr": pr},
                                headers={"Authorization": "Bearer test-deployment-token"},
                                content=binary_bundle({"echor": elf_binary(sha.encode())}),
                            )
                            assert response.status_code == 201, response.text
                            return str(response.json()["id"])

                        async def admit(identifier: str, revision: str) -> dict[str, str]:
                            response = await client.post(
                                f"/api/environments/{identifier}/sessions",
                                headers={"Origin": url},
                                json={"revision": revision},
                            )
                            assert response.status_code == 201, response.text
                            return response.json()  # type: ignore[no-any-return]

                        identifier = await publish("a" * 40)
                        first = await admit(identifier, "a" * 40)
                        old_artifact = app.state.store.get(identifier).artifact_id
                        ws_url = (
                            url.replace("http:", "ws:")
                            + f"/ws/{identifier}?session_id={first['id']}&revision={first['revision']}"
                        )
                        assert first["credential"] not in ws_url
                        async with connect(ws_url, origin=Origin(url)) as ws:
                            await ws.send(
                                json.dumps({"type": "auth", "credential": first["credential"]})
                            )
                            authenticated = json.loads(await ws.recv())
                            assert authenticated["type"] == "authenticated"
                            assert authenticated["absolute_deadline"] == first["absolute_deadline"]
                            assert await ws.recv() == b"ready\n"
                            await publish("b" * 40)
                            await app.state.service.cleanup_inactive()
                            # Retiring the old artifact must retain its mounted bytes for the old shell.
                            assert (config.data_dir / "artifacts" / str(old_artifact)).exists()
                            payload = json.dumps(
                                {"type": "input", "data": "é漢🙂\n"}, ensure_ascii=False
                            ).encode()
                            split = payload.index("🙂".encode()) + 2
                            await ws.send([payload[:split], payload[split:]], text=True)
                            assert await ws.recv() == "é漢🙂\n".encode()
                        second = await admit(identifier, "b" * 40)
                        assert second["id"] != first["id"]
                        stopped = await client.delete(
                            f"/api/environments/{identifier}/sessions/{first['id']}",
                            headers={
                                "Origin": url,
                                "Authorization": "Bearer " + first["credential"],
                            },
                        )
                        assert stopped.status_code == 200
                        sibling = await admit(await publish("c" * 40, 456), "c" * 40)
                        closed = await client.delete(
                            "/api/pull-requests/123",
                            headers={"Authorization": "Bearer test-deployment-token"},
                        )
                        assert closed.status_code == 200
                        assert (
                            await client.get(f"/api/environments/{identifier}")
                        ).status_code == 404
                        sibling_binding = app.state.sessions.get(UUID(sibling["id"]))
                        assert (
                            await api.get(f"/v1/sandboxes/{sibling_binding.sandbox.id}")
                        ).json()["state"] in {"accepted", "provisioning", "ready"}
                        assert agent.reserved_count == 1
                finally:
                    server.should_exit = True
                    await asyncio.wait_for(task, 5)
                    sock.close()
                    assert agent.reserved_count == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("finish", [True, False])
def test_authenticated_readiness_wait_is_not_an_abandoned_page_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: bool
) -> None:
    import asyncio
    import json
    from types import SimpleNamespace
    from uuid import UUID

    from playground import boxie_client as client_module
    from playground import service as service_module
    from websockets.asyncio.client import connect
    from websockets.typing import Origin

    from .boxie_network import boxie_network, product_server
    from .helpers import binary_bundle, elf_binary

    async def exercise() -> None:
        async with boxie_network(tmp_path / "agent") as network:
            gate = asyncio.Event()
            network.daemon.create_gate = gate
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                now = time.time()
                monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: now))
                response = await product.http.post(
                    "/api/environments",
                    params={"sha": "a" * 40, "pr": 123},
                    headers={"Authorization": "Bearer test-deployment-token"},
                    content=binary_bundle({"echor": elf_binary()}),
                )
                identifier = response.json()["id"]
                origin = str(product.http.base_url).rstrip("/")
                response = await product.http.post(
                    f"/api/environments/{identifier}/sessions",
                    headers={"Origin": origin},
                    json={"revision": "a" * 40},
                )
                binding = response.json()
                polled = asyncio.Event()
                read = client_module.bounded_response

                async def observe(response: aiohttp.ClientResponse) -> bytes:
                    data = await read(response)
                    if response.method == "GET" and "/sandboxes/" in response.url.path:
                        polled.set()
                    return data

                monkeypatch.setattr(client_module, "bounded_response", observe)
                try:
                    url = (
                        origin.replace("http:", "ws:")
                        + f"/ws/{identifier}?session_id={binding['id']}&revision={binding['revision']}"
                    )
                    async with connect(url, origin=Origin(origin)) as ws:
                        await ws.send(
                            json.dumps({"type": "auth", "credential": binding["credential"]})
                        )
                        await asyncio.wait_for(polled.wait(), 1)
                        now += 31
                        await product.service.recover(startup=False)
                        saved = product.service.sessions.get(UUID(binding["id"]))
                        assert (
                            saved is not None
                            and not saved.pending_stop
                            and saved.sandbox.state not in {"terminating", "terminated", "failed"}
                        )
                        if finish:
                            gate.set()
                            assert json.loads(await ws.recv())["type"] == "authenticated"
                            assert await ws.recv() == b"ready\n"
                        else:
                            from websockets.exceptions import ConnectionClosedError

                            now += 10
                            await product.service.recover(startup=False)
                            with pytest.raises(ConnectionClosedError):
                                await ws.recv()
                            saved = product.service.sessions.get(UUID(binding["id"]))
                            assert saved is not None and saved.attachment_deadline is None
                            assert saved.pending_stop or saved.sandbox.state == "terminated"
                finally:
                    gate.set()

    asyncio.run(exercise())
