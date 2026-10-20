"""Repeated preview cleanup must not restart confirmed remote stops."""

from uuid import UUID

import pytest
from cli_playground.boxie_client import SandboxState
from cli_playground.service import CliPlaygroundService
from fastapi.testclient import TestClient

from .boxie_fake import FakeBoxie
from .helpers import admission, call, upload


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
    call(api_client, lambda: service.close_pull_request(123))
    saved = call(api_client, lambda: service.sessions.get(UUID(binding["id"])))
    assert saved is not None and saved.sandbox.state == final_state and not saved.pending_stop
    before = saved.model_copy(deep=True)
    for _ in range(3):
        call(api_client, lambda: service.cleanup_inactive())
    assert boxie.stops == [saved.sandbox.id]
    assert call(api_client, lambda: service.sessions.get(saved.id)) == before
