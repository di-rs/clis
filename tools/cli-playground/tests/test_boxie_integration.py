"""Product admission contract, exercised through the public HTTP boundary."""

from pathlib import Path

import aiohttp
import pytest
from boxie_test_support.terminal_network import attach
from fastapi.testclient import TestClient

from .helpers import MutableClock, upload


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


def test_separate_product_api_supervisor_publish_terminal_reset_and_pr_close(
    tmp_path: Path,
) -> None:
    import asyncio
    import json
    from uuid import UUID

    from websockets.asyncio.client import connect
    from websockets.typing import Origin

    from .boxie_network import admit, boxie_network, product_server, publish, socket_url

    async def exercise() -> None:
        async with boxie_network(tmp_path / "supervisor") as network:
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                identifier = (await publish(product, "a" * 40)).json()["id"]
                first = await admit(product, identifier, "a" * 40)
                old_artifact = product.app.state.store.get(identifier).artifact_id
                origin = Origin(str(product.http.base_url).rstrip("/"))
                async with connect(socket_url(product, identifier, first), origin=origin) as ws:
                    await ws.send(json.dumps({"type": "auth", "credential": first["credential"]}))
                    assert (
                        json.loads(await ws.recv())["absolute_deadline"]
                        == first["absolute_deadline"]
                    )
                    epoch, payload = await attach(ws)
                    assert payload == b"ready\n"
                    await publish(product, "b" * 40)
                    await product.service.cleanup_inactive()
                    assert (
                        network.config.data_dir / "artifacts" / str(old_artifact) / "files"
                    ).exists()
                    payload = json.dumps(
                        {"type": "input", "epoch": epoch, "source": "user", "data": "é漢🙂\n"},
                        ensure_ascii=False,
                    ).encode()
                    split = payload.index("🙂".encode()) + 2
                    await ws.send([payload[:split], payload[split:]], text=True)
                    assert await ws.recv() == (2).to_bytes(8, "big") + "é漢🙂\n".encode()
                second = await admit(product, identifier, "b" * 40)
                assert second["id"] != first["id"]
                stopped = await product.http.delete(
                    f"/api/environments/{identifier}/sessions/{first['id']}",
                    headers={
                        "Origin": str(origin),
                        "Authorization": "Bearer " + first["credential"],
                    },
                )
                assert stopped.status_code == 200
                sibling = await admit(
                    product, (await publish(product, "c" * 40, 456)).json()["id"], "c" * 40
                )
                closed = await product.http.delete(
                    "/api/pull-requests/123",
                    headers={"Authorization": "Bearer test-deployment-token"},
                )
                assert closed.status_code == 200
                binding = product.service.sessions.get(UUID(sibling["id"]))
                assert binding is not None
                assert (await network.api.get(f"/v1/sandboxes/{binding.sandbox.id}")).json()[
                    "state"
                ] in {"accepted", "provisioning", "ready"}

    asyncio.run(exercise())


@pytest.mark.parametrize("finish", [True, False])
def test_authenticated_readiness_wait_is_not_an_abandoned_page_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: bool
) -> None:
    import asyncio
    import json
    from uuid import UUID

    from cli_playground import boxie_client as client_module
    from websockets.asyncio.client import connect
    from websockets.typing import Origin

    from .boxie_network import boxie_network, product_server
    from .helpers import binary_bundle, elf_binary

    async def exercise() -> None:
        async with boxie_network(tmp_path / "supervisor") as network:
            gate = asyncio.Event()
            network.daemon.create_gate = gate
            clock = MutableClock()
            async with product_server(
                tmp_path / "product", str(network.api.base_url), clock=clock
            ) as product:
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
                        clock.now += 31
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
                            assert (await attach(ws))[1] == b"ready\n"
                        else:
                            from websockets.exceptions import ConnectionClosedError

                            clock.now += 10
                            await product.service.recover(startup=False)
                            with pytest.raises(ConnectionClosedError):
                                await ws.recv()
                            saved = product.service.sessions.get(UUID(binding["id"]))
                            assert saved is not None and saved.attachment_deadline is None
                            assert saved.pending_stop or saved.sandbox.state == "terminated"
                finally:
                    gate.set()

    asyncio.run(exercise())
