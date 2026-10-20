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


@pytest.mark.parametrize("final_state", ["terminated", "failed"])
@pytest.mark.parametrize("legacy_origin", [False, True])
def test_repeated_stop_after_cleanup_preserves_first_origin(
    service: CliPlaygroundService,
    api_client: TestClient,
    boxie: FakeBoxie,
    final_state: SandboxState,
    legacy_origin: bool,
) -> None:
    from .helpers import ORIGIN

    preview = upload(api_client)
    binding = admission(api_client, preview["id"], "a" * 40)
    boxie.termination_state = final_state
    path = f"/api/environments/{preview['id']}/sessions/{binding['id']}"
    headers = {**ORIGIN, "Authorization": "Bearer " + binding["credential"]}
    first = api_client.delete(path, headers=headers)
    assert first.status_code == 200
    saved = service.sessions.get(UUID(binding["id"]))
    assert saved is not None and not saved.pending_stop
    assert saved.stop_request_id == UUID(first.headers["x-request-id"])
    if legacy_origin:
        saved.stop_request_id = None
        service.sessions.save(saved)
    expected = saved.stop_request_id
    repeated = api_client.delete(path, headers=headers)
    assert repeated.status_code == 200
    assert repeated.headers["x-request-id"] != first.headers["x-request-id"]
    restored = service.sessions.get(saved.id)
    assert restored is not None and restored.stop_request_id == expected
    assert boxie.stops == [saved.sandbox.id]
