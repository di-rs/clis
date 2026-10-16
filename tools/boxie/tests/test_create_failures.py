"""Creation failures release capacity only when absence is conclusive."""

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from aiodocker import Docker
from aiodocker.containers import DockerContainers
from aiodocker.exceptions import DockerError
from boxie.sandbox.docker import DockerBackend
from boxie_test_support.orchestrator import (
    Clock,
    DockerDaemon,
    configuration,
    live,
    request,
    settled,
    upload,
)


@pytest.mark.parametrize("status", [400, 404, 409, 500, None])
def test_create_response_controls_capacity_and_artifact_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int | None
) -> None:
    async def exercise() -> None:
        daemon, clock = DockerDaemon(), Clock()
        backend = DockerBackend()
        backend.client = MagicMock(spec=Docker)
        backend.client.containers = MagicMock(spec=DockerContainers)
        backend.client.containers.create = AsyncMock(
            side_effect=TimeoutError() if status is None else DockerError(status, "rejected")
        )
        monkeypatch.setattr(daemon, "create", backend.create)
        async with live(configuration(tmp_path, max_sandboxes=1), daemon, clock) as (client, owner):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            definitive = status in (400, 404)
            result = await settled(client, identifier, "failed" if definitive else "terminating")
            assert result["reason"] == "provisioning_failed"
            assert result["cleanup_pending"] is not definitive
            assert owner.reserved_count == (0 if definitive else 1)
            await client.delete(f"/v1/artifacts/{artifact}", params={"consumer_id": "one"})
            assert owner.artifact_exists(artifact) is not definitive
            if not definitive:
                # Elapsed time alone must never turn uncertain absence into confirmation.
                clock.advance(7200)
                await asyncio.sleep(0.03)
                assert owner.records[UUID(identifier)].creation_uncertain
                assert owner.reserved_count == 1
            replacement = await upload(client)
            response = await client.post("/v1/sandboxes", json=request(replacement, "next"))
            assert response.status_code == (200 if definitive else 429)

    asyncio.run(exercise())


@pytest.mark.parametrize("phase", ["attach", "start"])
def test_provisioning_crossing_deadline_never_becomes_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async def exercise() -> None:
        daemon, clock = DockerDaemon(), Clock()
        original = getattr(daemon, phase)

        async def crossing(name: str) -> object:
            result: object = await original(name)
            clock.advance(3600)
            return result

        monkeypatch.setattr(daemon, phase, crossing)
        config = replace(configuration(tmp_path), sweep_seconds=10)
        async with live(config, daemon, clock) as (client, owner):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            result = await settled(client, identifier, "terminated")
            assert result["reason"] == "expired"
            assert owner.reserved_count == 0 and not daemon.configs
            if phase == "attach":
                assert "start" not in daemon.events

    asyncio.run(exercise())
