"""Live lifetime policy through authenticated orchestrator HTTP and terminal input."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from boxie.domain.models import TerminationReason
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
from websockets.exceptions import InvalidStatus


def terminal_url(client: httpx.AsyncClient, identifier: str) -> str:
    return (
        str(client.base_url).replace("http:", "ws:")
        + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
    )


def test_never_attached_grace_starts_at_ready_and_releases_resources(tmp_path: Path) -> None:
    async def exercise() -> None:
        clock, daemon = Clock(), DockerDaemon()
        daemon.create_gate = asyncio.Event()
        async with live(configuration(tmp_path), daemon, clock) as (client, orchestrator):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await daemon.created.wait()
            clock.advance(200)
            daemon.create_gate.set()
            await settled(client, identifier, "ready")
            await client.delete(f"/v1/artifacts/{artifact}", params={"consumer_id": "one"})
            clock.advance(119)
            await asyncio.sleep(0.03)
            assert orchestrator.reserved_count == 1 and orchestrator.artifact_exists(artifact)
            clock.advance(1)
            result = await settled(client, identifier, "terminated")
            assert result["reason"] == "disconnected"
            assert orchestrator.reserved_count == 0 and not daemon.configs
            assert not orchestrator.artifact_exists(artifact)

    asyncio.run(exercise())


def test_first_attachment_before_grace_clears_it_but_cannot_reattach(tmp_path: Path) -> None:
    async def exercise() -> None:
        clock, daemon = Clock(), DockerDaemon()
        async with live(configuration(tmp_path), daemon, clock) as (client, _orchestrator):
            identifier = (
                await client.post("/v1/sandboxes", json=request(await upload(client)))
            ).json()["id"]
            await settled(client, identifier, "ready")
            clock.advance(119)
            url = terminal_url(client, identifier)
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                clock.advance(2)
                await ws.send(b"still attached")
                assert await ws.recv() == b"still attached"
                await asyncio.sleep(0.03)
                assert not daemon.removed
            await asyncio.sleep(0.03)
            with pytest.raises(InvalidStatus) as denial:
                async with connect(url, additional_headers=AUTH):
                    pytest.fail("reattachment accepted")
            assert denial.value.response.status_code == 409
            clock.advance(120)
            assert (await settled(client, identifier, "terminated"))["reason"] == "disconnected"

    asyncio.run(exercise())


def test_late_first_attachment_rejected_even_before_sweep(tmp_path: Path) -> None:
    async def exercise() -> None:
        clock = Clock()
        config = replace(configuration(tmp_path), sweep_seconds=10)
        async with live(config, DockerDaemon(), clock) as (client, _orchestrator):
            identifier = (
                await client.post("/v1/sandboxes", json=request(await upload(client)))
            ).json()["id"]
            await settled(client, identifier, "ready")
            clock.advance(120)
            with pytest.raises(InvalidStatus) as denial:
                async with connect(terminal_url(client, identifier), additional_headers=AUTH):
                    pytest.fail("late first attachment accepted")
            assert denial.value.response.status_code == 409

    asyncio.run(exercise())


def test_scheduled_idle_expiry_rechecks_input_accepted_over_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        clock, daemon = Clock(), DockerDaemon()
        async with live(configuration(tmp_path), daemon, clock) as (client, orchestrator):
            identifier = (
                await client.post("/v1/sandboxes", json=request(await upload(client)))
            ).json()["id"]
            await settled(client, identifier, "ready")
            selected, resume = asyncio.Event(), asyncio.Event()
            cleanup = orchestrator._cleanup

            async def delayed(record: Any, reason: TerminationReason, **kwargs: Any) -> Any:
                if reason == TerminationReason.IDLE:
                    selected.set()
                    await resume.wait()
                return await cleanup(record, reason, **kwargs)

            monkeypatch.setattr(orchestrator, "_cleanup", delayed)
            async with connect(terminal_url(client, identifier), additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                clock.advance(600)
                await asyncio.wait_for(selected.wait(), 1)
                try:
                    await ws.send(b"accepted after expiry was scheduled")
                    assert (
                        await asyncio.wait_for(ws.recv(), 1)
                        == b"accepted after expiry was scheduled"
                    )
                finally:
                    resume.set()
                await asyncio.wait_for(orchestrator._cleanup_tasks[UUID(identifier)], 1)
                result = await client.get(
                    f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"}
                )
                assert result.json()["state"] == "ready"
                assert orchestrator.reserved_count == 1 and not daemon.removed
                clock.advance(599)
                await asyncio.sleep(0.03)
                assert not daemon.removed
                clock.advance(1)
                assert (await settled(client, identifier, "terminated"))["reason"] == "idle"

    asyncio.run(exercise())


@pytest.mark.parametrize("wall_step", [-7200, 7200])
@pytest.mark.parametrize("timer", ["initial", "disconnected", "idle", "absolute"])
def test_live_deadlines_ignore_wall_steps(tmp_path: Path, wall_step: int, timer: str) -> None:
    async def exercise() -> None:
        clock, daemon = Clock(), DockerDaemon()
        async with live(configuration(tmp_path), daemon, clock) as (client, orchestrator):
            identifier = (
                await client.post("/v1/sandboxes", json=request(await upload(client)))
            ).json()["id"]
            original = await settled(client, identifier, "ready")
            websocket = None
            try:
                if timer != "initial":
                    websocket = await connect(
                        terminal_url(client, identifier), additional_headers=AUTH
                    )
                    assert await websocket.recv() == b"ready\n"
                if timer == "disconnected":
                    assert websocket is not None
                    await websocket.close()
                    for _ in range(100):
                        if orchestrator.records[UUID(identifier)].disconnected_at is not None:
                            break
                        await asyncio.sleep(0.01)
                    assert orchestrator.records[UUID(identifier)].disconnected_at is not None
                clock.advance_wall(wall_step)
                await asyncio.sleep(0.03)
                assert not daemon.removed, "wall step alone must not expire a live sandbox"
                duration = 3600 if timer == "absolute" else 600 if timer == "idle" else 120
                if timer == "absolute":
                    assert websocket is not None
                    # Keep accepting input through 3599s; the immutable hard cap still wins.
                    for seconds in (599, 599, 599, 599, 599, 599, 5):
                        clock.advance_monotonic(seconds)
                        await websocket.send(b"active")
                        assert await websocket.recv() == b"active"
                else:
                    clock.advance_monotonic(duration - 1)
                await asyncio.sleep(0.03)
                assert not daemon.removed
                current = (
                    await client.get(f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"})
                ).json()
                persisted = json.loads(orchestrator.path.read_text())["records"][0]
                for field in ("created_at", "absolute_deadline"):
                    assert current[field] == original[field] == persisted[field]
                clock.advance_monotonic(1)
                result = await settled(client, identifier, "terminated")
                assert result["reason"] == (
                    "expired"
                    if timer == "absolute"
                    else "idle"
                    if timer == "idle"
                    else "disconnected"
                )
                assert orchestrator.reserved_count == 0 and not daemon.configs
            finally:
                if websocket is not None:
                    await websocket.close()

    asyncio.run(exercise())
