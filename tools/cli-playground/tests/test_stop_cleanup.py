"""Repeated preview cleanup must not restart confirmed remote stops."""

import asyncio
from uuid import UUID

import pytest
from cli_playground.boxie_client import SandboxState
from cli_playground.service import CliPlaygroundService
from fastapi.testclient import TestClient

from .boxie_fake import FakeBoxie
from .helpers import admission, upload


@pytest.mark.parametrize("final_state", ["terminated", "failed"])
def test_removed_preview_does_not_repeat_confirmed_stop(
    service: CliPlaygroundService,
    api_client: TestClient,
    boxie: FakeBoxie,
    final_state: SandboxState,
) -> None:
    preview = upload(api_client)
    binding = admission(api_client, preview["id"], "a" * 40)
    boxie.termination_state = final_state
    asyncio.run(service.close_pull_request(123))
    saved = service.sessions.get(UUID(binding["id"]))
    assert saved is not None and saved.sandbox.state == final_state and not saved.pending_stop
    path = service.sessions.root / "bindings" / f"{binding['id']}.json"
    before = path.stat().st_mtime_ns
    for _ in range(3):
        asyncio.run(service.cleanup_inactive())
    assert boxie.stops == [saved.sandbox.id]
    assert path.stat().st_mtime_ns == before
