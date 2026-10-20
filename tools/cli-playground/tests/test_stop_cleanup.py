"""Repeated preview cleanup must not restart confirmed remote stops."""

from uuid import UUID

import pytest
from cli_playground.sandbox import SandboxState
from cli_playground.service import CliPlaygroundService
from cli_playground.storage import EnvironmentStore
from fastapi.testclient import TestClient

from .boxie_fake import FakeBoxie
from .helpers import admission, call, upload


@pytest.mark.parametrize("final_state", ["terminated", "failed"])
def test_removed_preview_does_not_repeat_confirmed_stop(
    service: CliPlaygroundService,
    api_client: TestClient,
    boxie: FakeBoxie,
    final_state: SandboxState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = upload(api_client)
    binding = admission(api_client, preview["id"], "a" * 40)
    boxie.termination_state = final_state
    call(api_client, lambda: service.close_pull_request(123))
    saved = call(
        api_client, lambda: service.sessions(lambda repository: repository.get(UUID(binding["id"])))
    )
    assert saved is not None and saved.sandbox.state == final_state and not saved.pending_stop
    before = saved.model_copy(deep=True)
    assert (
        call(
            api_client, lambda: service.store(lambda repository: repository.inactive_identifiers())
        )
        == set()
    )

    def no_per_binding_lookup(*args: object, **kwargs: object) -> None:
        pytest.fail("cleanup must select inactive previews in bulk")

    monkeypatch.setattr(EnvironmentStore, "record", no_per_binding_lookup)
    for _ in range(3):
        call(api_client, lambda: service.cleanup_inactive())
    assert boxie.stops == [saved.sandbox.id]
    assert (
        call(api_client, lambda: service.sessions(lambda repository: repository.get(saved.id)))
        == before
    )


def test_tombstone_with_unfinished_binding_still_retries_stop(
    service: CliPlaygroundService, api_client: TestClient, boxie: FakeBoxie
) -> None:
    preview = upload(api_client)
    binding = admission(api_client, preview["id"], "a" * 40)
    call(api_client, lambda: service.store(lambda repository: repository.close_pull_request(123)))
    call(api_client, lambda: service.store(lambda repository: repository.remove({preview["id"]})))
    assert call(
        api_client, lambda: service.store(lambda repository: repository.inactive_identifiers())
    ) == {preview["id"]}
    call(api_client, lambda: service.cleanup_inactive())
    saved = call(
        api_client, lambda: service.sessions(lambda repository: repository.get(UUID(binding["id"])))
    )
    assert saved is not None and not saved.pending_stop
    assert boxie.stops == [saved.sandbox.id]
    assert (
        call(
            api_client, lambda: service.store(lambda repository: repository.inactive_identifiers())
        )
        == set()
    )
