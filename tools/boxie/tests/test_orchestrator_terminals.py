"""Orchestrator behavior across actual authenticated HTTP and WebSocket transports."""

import asyncio
from pathlib import Path

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


def test_input_alone_resets_idle_and_never_absolute_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _orchestrator):
            artifact = await upload(client)
            body = request(artifact)
            body["spec"]["policy"].update({"idle_seconds": 20, "max_seconds": 30})
            identifier = (await client.post("/v1/sandboxes", json=body)).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                clock.advance(15)
                await ws.send(b"input")
                assert await ws.recv() == b"input"
                clock.advance(10)
                await asyncio.sleep(0.03)
                assert not daemon.removed
                clock.advance(5)
                assert (await settled(client, identifier, "terminated"))["reason"] == "expired"

    asyncio.run(exercise())


def test_output_cap_and_status_are_orchestrator_enforced(tmp_path: Path) -> None:
    import json

    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        profiles = json.loads(config.profiles_file.read_text())
        profiles["shell"]["limits"] = {"output_bytes": 10}
        config.profiles_file.write_text(json.dumps(profiles))
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
                await ws.send("ééé".encode())
                assert await ws.recv() == "éé".encode()
                assert json.loads(await ws.recv()) == {"type": "status", "reason": "output_limit"}
            assert (await settled(client, identifier, "terminated"))["reason"] == "output_limit"
            assert orchestrator.reserved_count == 0

    asyncio.run(exercise())


def test_idle_resize_and_output_do_not_extend_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _orchestrator):
            artifact = await upload(client)
            body = request(artifact)
            body["spec"]["policy"]["idle_seconds"] = 20
            identifier = (await client.post("/v1/sandboxes", json=body)).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                clock.advance(15)
                await ws.send('{"type":"resize","columns":100,"rows":30}')
                next(iter(daemon.terminals.values())).queue.put_nowait(b"background output")
                assert await ws.recv() == b"background output"
                clock.advance(5)
                assert (await settled(client, identifier, "terminated"))["reason"] == "idle"

    asyncio.run(exercise())
