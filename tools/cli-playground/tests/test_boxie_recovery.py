"""Failure and restart contracts across actual product/API/agent servers."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest
from playground import boxie_client as client_module
from playground.boxie_client import BoxieUnavailable
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.typing import Origin

from .boxie_network import admit, boxie_network, product_server, publish, socket_url


@pytest.mark.parametrize("failure", ["reply", "commit"])
def test_failed_publication_preserves_ready_revision_and_recovers_exact_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    async def exercise() -> None:
        async with boxie_network(tmp_path / "agent") as network:
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                first = await publish(product, "a" * 40)
                assert first.status_code == 201
                identifier = first.json()["id"]
                original = product.service.store.get(identifier)
                assert original is not None
                read = client_module.bounded_response
                with monkeypatch.context() as fault:
                    if failure == "reply":

                        async def lose_reply(response: aiohttp.ClientResponse) -> bytes:
                            data = await read(response)
                            if response.method == "PUT":
                                raise BoxieUnavailable("lost upload reply")
                            return data

                        fault.setattr(client_module, "bounded_response", lose_reply)
                    else:

                        def fail_commit(*args: object) -> None:
                            raise OSError("injected metadata commit failure")

                        fault.setattr(product.service.store, "_write_record", fail_commit)
                    failed = await publish(product, "b" * 40)
                    assert failed.status_code == (503 if failure == "reply" else 500)
                assert product.service.store.get(identifier) == original
                old_page = await admit(product, identifier, "a" * 40)
                origin = str(product.http.base_url).rstrip("/")
                async with connect(
                    socket_url(product, identifier, old_page), origin=Origin(origin)
                ) as ws:
                    await ws.send(
                        json.dumps({"type": "auth", "credential": old_page["credential"]})
                    )
                    assert json.loads(await ws.recv())["type"] == "authenticated"
                    assert await ws.recv() == b"ready\n"
                (pending,) = product.service.sessions.uploads()
                artifact_id = pending.artifact.artifact_id
                # Both failures occur after the real agent has accepted these bytes.
                assert (network.config.data_dir / "artifacts" / str(artifact_id)).exists()
            async with product_server(tmp_path / "product", str(network.api.base_url)) as restarted:
                recovered = restarted.service.store.get(identifier)
                assert recovered is not None and recovered.revision == "b" * 40
                assert recovered.artifact_id == artifact_id
                assert not restarted.service.sessions.uploads()
                assert not restarted.service.sessions.retirements()
                assert (
                    restarted.service.sessions.root / "retirements" / f"{original.artifact_id}.done"
                ).exists()

    asyncio.run(exercise())


def test_capability_failures_and_restart_preserve_claimed_session_while_cleaning_others(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with boxie_network(tmp_path / "agent") as network:
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                first = await publish(product, "a" * 40)
                sibling = await publish(product, "b" * 40, 456)
                identifier, other = first.json()["id"], sibling.json()["id"]
                stopped = await admit(product, identifier, "a" * 40)
                healthy = await admit(product, other, "b" * 40)
                abandoned = await admit(product, other, "b" * 40)
                origin = str(product.http.base_url).rstrip("/")
                # A copied UUID or credential from another preview cannot authorize it.
                for owner, credential in [
                    (identifier, "wrong-credential"),
                    (other, stopped["credential"]),
                ]:
                    denied = await product.http.delete(
                        f"/api/environments/{owner}/sessions/{stopped['id']}",
                        headers={"Origin": origin, "Authorization": "Bearer " + credential},
                    )
                    assert denied.status_code == 404
                async with connect(
                    socket_url(product, identifier, stopped), origin=Origin(origin)
                ) as ws:
                    await ws.send(json.dumps({"type": "auth", "credential": healthy["credential"]}))
                    with pytest.raises(ConnectionClosedError) as closed:
                        await ws.recv()
                    assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008
                with pytest.raises(InvalidStatus) as denied_socket:
                    async with connect(socket_url(product, other, stopped), origin=Origin(origin)):
                        pytest.fail("cross-preview socket was accepted")
                assert denied_socket.value.response.status_code == 403
                # Valid auth still works after the rejected capability attempt.
                async with connect(
                    socket_url(product, other, healthy), origin=Origin(origin)
                ) as ws:
                    await ws.send(json.dumps({"type": "auth", "credential": healthy["credential"]}))
                    assert json.loads(await ws.recv())["type"] == "authenticated"
                    assert await ws.recv() == b"ready\n"
                saved = product.service.sessions.get(UUID(healthy["id"]))
                assert saved is not None and saved.attached
                healthy_view = saved.sandbox
                network.daemon.fail_delete = True
                closed_pr = await product.http.delete(
                    "/api/pull-requests/123",
                    headers={"Authorization": "Bearer test-deployment-token"},
                )
                assert closed_pr.status_code == 200
                pending = product.service.sessions.get(UUID(stopped["id"]))
                orphan = product.service.sessions.get(UUID(abandoned["id"]))
                assert pending is not None and pending.pending_stop
                assert orphan is not None
                assert product.service.store.record(identifier) is None
            network.daemon.fail_delete = False
            async with product_server(tmp_path / "product", str(network.api.base_url)) as restarted:
                for sandbox_id in (pending.sandbox.id, orphan.sandbox.id):
                    response = await network.api.get(f"/v1/sandboxes/{sandbox_id}")
                    assert response.json()["state"] == "terminated"
                response = await network.api.get(f"/v1/sandboxes/{healthy_view.id}")
                assert response.json()["state"] == "ready"
                assert response.json()["absolute_deadline"] == healthy["absolute_deadline"]
                retained = restarted.service.sessions.get(UUID(healthy["id"]))
                assert retained is not None and retained.attached and not retained.pending_stop
                assert network.agent.reserved_count == 1

    asyncio.run(exercise())


def test_malformed_stop_reply_keeps_the_original_durable_binding_for_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        async with boxie_network(tmp_path / "agent") as network:
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                identifier = (await publish(product, "a" * 40)).json()["id"]
                admitted = await admit(product, identifier, "a" * 40)
                binding = product.service.sessions.get(UUID(admitted["id"]))
                assert binding is not None
                origin = str(product.http.base_url).rstrip("/")
                async with connect(
                    socket_url(product, identifier, admitted), origin=Origin(origin)
                ) as ws:
                    await ws.send(
                        json.dumps({"type": "auth", "credential": admitted["credential"]})
                    )
                    assert json.loads(await ws.recv())["type"] == "authenticated"
                    assert await ws.recv() == b"ready\n"
            # A fresh HTTP adapter has no in-memory response history. The saved binding
            # must still reject a different but otherwise valid lifetime.
            async with product_server(tmp_path / "product", str(network.api.base_url)) as product:
                binding = product.service.sessions.get(UUID(admitted["id"]))
                assert binding is not None
                read = client_module.bounded_response

                async def corrupt(response: aiohttp.ClientResponse) -> bytes:
                    data = await read(response)
                    if response.method == "DELETE" and "/sandboxes/" in response.url.path:
                        view = json.loads(data)
                        view["absolute_deadline"] = (
                            datetime.fromisoformat(view["absolute_deadline"]) - timedelta(seconds=1)
                        ).isoformat()
                        return json.dumps(view).encode()
                    return data

                with monkeypatch.context() as fault:
                    fault.setattr(client_module, "bounded_response", corrupt)
                    response = await product.http.delete(
                        f"/api/environments/{identifier}/sessions/{admitted['id']}",
                        headers={
                            "Origin": str(product.http.base_url).rstrip("/"),
                            "Authorization": "Bearer " + admitted["credential"],
                        },
                    )
                    assert response.status_code == 200
                saved = product.service.sessions.get(binding.id)
                assert saved is not None and saved.pending_stop and saved.sandbox == binding.sandbox
                await product.service.retry_stops()
                retried = product.service.sessions.get(binding.id)
                assert retried is not None and not retried.pending_stop
                assert retried.sandbox.absolute_deadline == binding.sandbox.absolute_deadline
            async with product_server(tmp_path / "product", str(network.api.base_url)) as restarted:
                saved = restarted.service.sessions.get(binding.id)
                assert saved is not None and not saved.pending_stop
                assert saved.sandbox.id == binding.sandbox.id
                assert saved.sandbox.absolute_deadline == binding.sandbox.absolute_deadline
                assert saved.sandbox.state == "terminated"

    asyncio.run(exercise())
