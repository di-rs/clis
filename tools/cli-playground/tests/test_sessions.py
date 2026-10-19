"""Page capability ownership, terminal authentication, and pinned product revisions."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from cli_playground.app import create_app
from cli_playground.boxie_client import BoxieUnavailable, SandboxView
from cli_playground.service import CliPlaygroundService
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .boxie_fake import FakeBoxie
from .helpers import (
    ORIGIN,
    Settings,
    admission,
    attach_terminal,
    build_event,
    build_upload,
    socket_url,
    upload,
)


def test_bound_session_survives_new_revision_and_empty_build(api_client: TestClient) -> None:
    deployed = build_upload(api_client).json()
    binding = admission(api_client, deployed["id"], "a" * 40 + "-10-1")
    assert build_upload(api_client, attempt=2, payload=b"new").status_code == 201
    # Admission is pinned, while a previously bound session may attach its old revision.
    assert (
        api_client.post(
            f"/api/environments/{deployed['id']}/sessions",
            headers=ORIGIN,
            json={"revision": binding["revision"]},
        ).status_code
        == 409
    )
    with api_client.websocket_connect(socket_url(binding), headers=ORIGIN) as ws:
        ws.send_json({"type": "auth", "credential": binding["credential"]})
        assert ws.receive_json()["type"] == "authenticated"
        attach_terminal(ws)
        assert build_event(api_client, "empty", run=11).status_code == 200
        ws.send_json({"type": "input", "epoch": 1, "source": "user", "data": "é漢🙂"})
        assert ws.receive_bytes() == (2).to_bytes(8, "big") + "é漢🙂".encode()
    assert api_client.get(f"/api/environments/{deployed['id']}").json()["sha"] is None


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "input", "data": "untrusted"},
        {"type": "resize", "cols": 80, "rows": 24},
        {"type": "auth", "credential": "wrong"},
        {"type": "auth", "credential": "x" * 2000},
        {"type": "auth", "credential": "x", "extra": True},
        "not json",
        b"binary",
    ],
)
def test_first_frame_must_authenticate_before_upstream_attach(
    boxie: FakeBoxie, api_client: TestClient, frame: object
) -> None:
    binding = admission(api_client, upload(api_client)["id"], "a" * 40)
    with api_client.websocket_connect(socket_url(binding), headers=ORIGIN) as ws:
        if isinstance(frame, bytes):
            ws.send_bytes(frame)
        elif isinstance(frame, str):
            ws.send_text(frame)
        else:
            ws.send_json(frame)
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 1008
    assert boxie.attachments == 0


def test_copied_ids_cannot_attach_or_delete_and_no_raw_credential_is_persisted(
    service: CliPlaygroundService,
    api_client: TestClient,
) -> None:
    first, second = upload(api_client), upload(api_client, pr=456)
    binding = admission(api_client, first["id"], "a" * 40)
    for identifier, token in [(first["id"], "wrong"), (second["id"], binding["credential"])]:
        response = api_client.delete(
            f"/api/environments/{identifier}/sessions/{binding['id']}",
            headers={**ORIGIN, "Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with api_client.websocket_connect(
            socket_url({**binding, "preview_id": second["id"]}), headers=ORIGIN
        ):
            pytest.fail("foreign preview accepted")
    root = service.settings.data_dir
    assert all(binding["credential"] not in p.read_text() for p in root.rglob("*.json"))
    response = api_client.delete(
        f"/api/environments/{first['id']}/sessions/{binding['id']}",
        headers={**ORIGIN, "Authorization": f"Bearer {binding['credential']}"},
    )
    assert response.status_code == 200
    saved = service.sessions.get(UUID(binding["id"]))
    assert saved is not None and saved.sandbox.state == "terminated"


def test_page_reload_gets_a_fresh_binding_and_backend_shutdown_keeps_claimed_sandbox(
    service: CliPlaygroundService,
    api_client: TestClient,
) -> None:
    identifier = upload(api_client)["id"]
    first, second = (
        admission(api_client, identifier, "a" * 40),
        admission(api_client, identifier, "a" * 40),
    )
    assert first["id"] != second["id"] and first["credential"] != second["credential"]
    with api_client.websocket_connect(socket_url(first), headers=ORIGIN) as ws:
        ws.send_json({"type": "auth", "credential": first["credential"]})
        assert ws.receive_json()["absolute_deadline"] == first["absolute_deadline"]
        attach_terminal(ws)
    asyncio.run(service.boxie.close())
    saved = service.sessions.get(UUID(first["id"]))
    assert saved is not None and saved.sandbox.state == "ready"


@pytest.mark.parametrize("expiry", [False, True])
def test_stop_failure_survives_preview_removal_and_restart_without_touching_other_pr(
    service: CliPlaygroundService,
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    expiry: bool,
) -> None:
    assert isinstance(service.boxie, FakeBoxie)
    client = service.boxie
    first = admission(api_client, upload(api_client)["id"], "a" * 40)
    second = admission(api_client, upload(api_client, pr=456)["id"], "a" * 40)
    # Claim B so restart recovery can distinguish a delivered session from an abandoned create.
    service.sessions.claimed(UUID(second["id"]))
    original = client.terminate

    async def unavailable(identifier: UUID, previous: SandboxView | None = None) -> SandboxView:
        raise BoxieUnavailable()

    monkeypatch.setattr(client, "terminate", unavailable)
    if expiry:
        record = service.store.record(first["preview_id"])
        assert record is not None
        record.expires_at = 0
        service.store._write_record(record)
        asyncio.run(service.cleanup_inactive())
    else:
        assert (
            api_client.delete(
                "/api/pull-requests/123", headers={"Authorization": "Bearer test-deployment-token"}
            ).status_code
            == 200
        )
    assert not (service.settings.data_dir / first["preview_id"]).exists()
    saved = service.sessions.get(UUID(first["id"]))
    assert saved is not None and saved.pending_stop
    monkeypatch.setattr(client, "terminate", original)
    with TestClient(create_app(service.settings, boxie=client), base_url="https://play.example"):
        saved_first = service.sessions.get(UUID(first["id"]))
        saved_second = service.sessions.get(UUID(second["id"]))
        assert saved_first is not None and saved_second is not None
        assert client.sandboxes[saved_first.sandbox.id].state == "terminated"
        assert client.sandboxes[saved_second.sandbox.id].state == "ready"


def test_pr_host_allows_only_matching_origin_scoped_session_mutations(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        public_url="https://play.example",
        preview_domain="play.example",
        deploy_token="test-deployment-token",
        boxie_url="http://boxie.internal",
        boxie_credential_file=tmp_path / "secret",
    )
    with TestClient(
        create_app(settings, boxie=FakeBoxie()), base_url=settings.public_url
    ) as client:
        identifier = upload(client)["id"]
        url = f"https://pr-123.play.example/api/environments/{identifier}/sessions"
        assert client.post(url, headers=ORIGIN, json={"revision": "a" * 40}).status_code == 403
        response = client.post(
            url, headers={"Origin": "https://pr-123.play.example"}, json={"revision": "a" * 40}
        )
        assert response.status_code == 201
        binding = response.json()
        assert (
            client.delete(
                url + "/" + binding["id"],
                headers={
                    "Origin": "https://pr-123.play.example",
                    "Authorization": "Bearer " + binding["credential"],
                },
            ).status_code
            == 200
        )
        assert (
            client.post(url, headers={"Origin": "https://pr-123.play.example"}, json={}).status_code
            == 422
        )


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "resize", "cols": 0, "rows": 24},
        {"type": "resize", "cols": 80, "rows": 301},
        {"type": "input", "data": "漢" * 6000},
    ],
)
def test_terminal_rejects_invalid_dimensions_and_encoded_byte_overflow(
    api_client: TestClient, frame: dict[str, object]
) -> None:
    binding = admission(api_client, upload(api_client)["id"], "a" * 40)
    with api_client.websocket_connect(socket_url(binding), headers=ORIGIN) as ws:
        ws.send_json({"type": "auth", "credential": binding["credential"]})
        assert ws.receive_json()["type"] == "authenticated"
        attach_terminal(ws)
        ws.send_json(frame)
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_bytes()
        assert error.value.code == 1008


def test_missing_auth_frame_times_out_before_upstream_connection(
    boxie: FakeBoxie, api_client: TestClient
) -> None:
    binding = admission(api_client, upload(api_client)["id"], "a" * 40)
    with api_client.websocket_connect(socket_url(binding), headers=ORIGIN) as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 1008
    assert boxie.attachments == 0
