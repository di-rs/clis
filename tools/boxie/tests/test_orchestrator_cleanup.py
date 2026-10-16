"""Orchestrator behavior across actual authenticated HTTP and WebSocket transports."""

import asyncio
from pathlib import Path

import pytest
from boxie_test_support.orchestrator import (
    AUTH,
    Clock,
    DockerDaemon,
    configuration,
    live,
    request,
    settled,
    upload,
)
from websockets.asyncio.client import connect


def test_cleanup_failure_retains_capacity_and_retired_artifact(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path, max_sandboxes=1), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, orchestrator):
            artifact = await upload(client)
            first = await client.post("/v1/sandboxes", json=request(artifact))
            identifier = first.json()["id"]
            await settled(client, identifier, "ready")
            daemon.fail_delete = True
            assert (
                await client.delete(f"/v1/artifacts/{artifact}", params={"consumer_id": "one"})
            ).status_code == 204
            deleted = await client.delete(
                f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"}
            )
            assert deleted.json()["cleanup_pending"] is True
            assert deleted.json()["state"] == "terminating"
            assert orchestrator.reserved_count == 1
            assert orchestrator.artifact_exists(artifact)
            daemon.fail_delete = False
            await settled(client, identifier, "terminated")
            assert orchestrator.reserved_count == 0
            assert not orchestrator.artifact_exists(artifact)
            assert (
                await client.post("/v1/sandboxes", json=request(artifact, "new"))
            ).status_code == 409

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("policy", "advance", "reason"),
    [({"max_seconds": 30}, 30, "expired"), ({"idle_seconds": 20}, 20, "idle")],
)
def test_deadlines_without_client_requests(
    tmp_path: Path, policy: dict[str, int], advance: int, reason: str
) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _orchestrator):
            artifact = await upload(client)
            body = request(artifact)
            body["spec"]["policy"].update(policy)
            identifier = (await client.post("/v1/sandboxes", json=body)).json()["id"]
            await settled(client, identifier, "ready")
            clock.advance(advance)
            await asyncio.sleep(0.05)
            assert daemon.removed
            assert (await settled(client, identifier, "terminated"))["reason"] == reason

    asyncio.run(exercise())


def test_failed_delete_closes_terminal_without_releasing_reference(tmp_path: Path) -> None:
    import json

    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, orchestrator):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                daemon.fail_delete = True
                response = await client.delete(
                    f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"}
                )
                assert response.json()["cleanup_pending"]
                assert json.loads(await asyncio.wait_for(ws.recv(), 0.5))["reason"] == "requested"
                assert next(iter(daemon.terminals.values())).closed
                assert orchestrator.reserved_count == 1 and orchestrator.artifact_exists(artifact)
            daemon.fail_delete = False
            await settled(client, identifier, "terminated")

    asyncio.run(exercise())


def test_restart_does_not_delete_wrong_generation_and_keeps_idempotency(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _orchestrator):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            name = next(iter(daemon.configs))
            daemon.configs[name]["Labels"]["boxie.generation"] = "2"
        assert not daemon.removed
        async with live(config, daemon, clock) as (client, orchestrator):
            assert orchestrator.reserved_count == 1
            result = await client.post("/v1/sandboxes", json=request(artifact))
            assert result.json()["id"] == identifier and result.json()["cleanup_pending"]
            assert not daemon.removed
            daemon.configs[name]["Labels"]["boxie.generation"] = "1"
            await settled(client, identifier, "terminated")
            assert orchestrator.reserved_count == 0

    asyncio.run(exercise())


def test_slow_cleanup_does_not_delay_another_sandbox_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _orchestrator):
            artifact = await upload(client)
            first = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, first, "ready")
            second_body = request(artifact, "second")
            second_body["spec"]["policy"]["max_seconds"] = 10
            second = (await client.post("/v1/sandboxes", json=second_body)).json()["id"]
            await settled(client, second, "ready")
            slow_name = next(
                name
                for name, value in daemon.configs.items()
                if value["Labels"]["boxie.sandbox"] == first
            )
            entered, release = asyncio.Event(), asyncio.Event()
            original = daemon.delete

            async def slow_delete(name: str) -> None:
                if name == slow_name:
                    entered.set()
                    await release.wait()
                await original(name)

            monkeypatch.setattr(daemon, "delete", slow_delete)
            removal = asyncio.create_task(
                client.delete(f"/v1/sandboxes/{first}", params={"consumer_id": "one"})
            )
            try:
                await entered.wait()
                clock.advance(10)
                await asyncio.sleep(0.1)
                result = await client.get(f"/v1/sandboxes/{second}", params={"consumer_id": "one"})
                assert result.json()["state"] == "terminated"
            finally:
                release.set()
                await removal

    asyncio.run(exercise())
