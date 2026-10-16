"""Repeated preview cleanup must not restart confirmed remote stops."""

import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playground.boxie_client import SandboxView
from playground.service import PlaygroundService

from .helpers import upload
from .test_sessions import admission


@pytest.mark.parametrize("final_state", ["terminated", "failed"])
def test_removed_preview_does_not_repeat_confirmed_stop(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, final_state: str
) -> None:
    preview = upload(api_client)
    binding = admission(api_client, preview["id"], "a" * 40)
    service: PlaygroundService = cast(FastAPI, api_client.app).state.service
    terminate = service.boxie.terminate
    stops: list[UUID] = []

    async def counted(identifier: UUID, previous: SandboxView | None = None) -> SandboxView:
        stops.append(identifier)
        result = await terminate(identifier, previous)
        return result.model_copy(update={"state": final_state})

    monkeypatch.setattr(service.boxie, "terminate", counted)
    asyncio.run(service.close_pull_request(123))
    saved = service.sessions.get(UUID(binding["id"]))
    assert saved is not None and saved.sandbox.state == final_state and not saved.pending_stop
    path = service.sessions.root / "bindings" / f"{binding['id']}.json"
    before = path.stat().st_mtime_ns
    for _ in range(3):
        asyncio.run(service.cleanup_inactive())
    assert stops == [saved.sandbox.id]
    assert path.stat().st_mtime_ns == before
