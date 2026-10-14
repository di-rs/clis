"""Exercise the public preview and authenticated deployment contract."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from playground.app import create_app
from playground.config import Settings
from starlette.websockets import WebSocketDisconnect

from .helpers import binary_bundle, elf_binary, upload


def test_one_pr_url_publishes_all_clis_and_tracks_the_latest_revision(
    api_client: TestClient,
) -> None:
    binaries = {"echor": elf_binary(), "catr": elf_binary(b"cat")}
    deployed = upload(api_client, clis=binaries)
    assert deployed["url"] == f"https://play.example/p/{deployed['id']}"
    metadata = api_client.get(f"/api/environments/{deployed['id']}").json()
    assert metadata == {
        "id": deployed["id"],
        "pr": 123,
        "sha": "a" * 40,
        "clis": ["catr", "echor"],
        "expires_at": metadata["expires_at"],
    }
    assert upload(api_client, clis=binaries) == deployed
    assert upload(api_client, cli="wcr", sha="b" * 40) == deployed
    assert api_client.get(f"/api/environments/{deployed['id']}").json()["clis"] == ["wcr"]
    assert upload(api_client, pr=456)["id"] != deployed["id"]


@pytest.mark.parametrize("change", ["binary", "cli_set"])
def test_a_published_revision_cannot_change_its_binary_or_cli_set(
    api_client: TestClient, change: str
) -> None:
    deployed = upload(api_client)
    binaries = (
        {"echor": elf_binary(b"different")}
        if change == "binary"
        else {
            "echor": elf_binary(),
            "catr": elf_binary(),
        }
    )
    response = api_client.post(
        "/api/environments",
        params={"sha": "a" * 40, "pr": 123},
        content=binary_bundle(binaries),
        headers={"Authorization": "Bearer test-deployment-token"},
    )
    assert response.status_code == 409
    assert api_client.get(f"/api/environments/{deployed['id']}").json()["clis"] == ["echor"]


def test_upload_requires_the_correct_deployment_token(api_client: TestClient) -> None:
    headers = {"Authorization": "Bearer wrong"}
    response = api_client.post(
        "/api/environments?sha=" + "a" * 40 + "&pr=123",
        content=binary_bundle({"echor": elf_binary()}),
        headers=headers,
    )
    assert response.status_code == 401


def test_streamed_upload_enforces_limit_and_removes_partial_files(
    api_client: TestClient, tmp_path: Path
) -> None:
    response = api_client.post(
        "/api/environments?sha=" + "a" * 40 + "&pr=123",
        content=binary_bundle({"echor": elf_binary(b"x" * 128)}),
        headers={"Authorization": "Bearer test-deployment-token"},
    )
    assert response.status_code == 413
    assert not list((tmp_path / "data").rglob("echor"))


def test_websocket_rejects_a_foreign_origin(api_client: TestClient) -> None:
    deployed = upload(api_client)
    headers = {"Origin": "https://evil.example"}
    with pytest.raises(WebSocketDisconnect) as error:
        with api_client.websocket_connect(
            f"wss://play.example/ws/{deployed['id']}?sha={'a' * 40}", headers=headers
        ):
            pytest.fail("an unauthorized origin opened a terminal")
    assert error.value.code == 1008


def test_unknown_preview_does_not_launch_terminal(api_client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as error:
        with api_client.websocket_connect(
            "wss://play.example/ws/" + "f" * 32 + "?sha=" + "a" * 40,
            headers={"Origin": "https://play.example"},
        ):
            pytest.fail("unknown environment opened a terminal")
    assert error.value.code == 1008


def test_expired_previews_are_unavailable_before_the_periodic_sweep(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deployed = upload(api_client)
    monkeypatch.setattr("playground.storage.time.time", lambda: 10**12)
    response = api_client.get(f"/api/environments/{deployed['id']}")
    assert response.status_code == 404
    assert (tmp_path / "data" / deployed["id"]).exists()


def test_preview_resolves_built_assets_under_static_prefix(api_client: TestClient) -> None:
    response = api_client.get("/p/" + "f" * 32)
    assert response.headers["cache-control"] == "no-store"
    assert 'src="/static/assets/app-test.js"' in response.text
    asset = api_client.get("/static/assets/app-test.js")
    assert asset.status_code == 200
    assert "document.title" in asset.text


def test_subdomain_urls_bind_http_and_websocket_to_the_same_preview(
    api_client: TestClient, tmp_path: Path
) -> None:
    settings = Settings(
        data_dir=tmp_path / "domains",
        public_url="https://play.example",
        preview_domain="play.example",
        deploy_token="test-deployment-token",
        sandbox_image="clis-sandbox:test",
    )
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        current, sibling = upload(client), upload(client, cli="catr", pr=456)
        authority = f"{current['id']}.play.example"
        assert current["url"] == f"https://{authority}/p/{current['id']}"
        metadata = client.get(f"https://{authority}/api/environments/{current['id']}")
        assert metadata.status_code == 200
        assert metadata.json()["clis"] == ["echor"]
        assert client.get(current["url"]).status_code == 200
        assert (
            client.get(f"https://{authority}/api/environments/{sibling['id']}").status_code == 404
        )
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(
                f"wss://{authority}/ws/{sibling['id']}?sha={'a' * 40}",
                headers={"Origin": f"https://{authority}"},
            ):
                pytest.fail("cross-preview terminal was accepted")
        assert error.value.code == 1008


def test_forwarded_headers_cannot_spoof_the_control_host(api_client: TestClient) -> None:
    response = api_client.get(
        "/healthz", headers={"Host": "evil.example", "X-Forwarded-Host": "play.example"}
    )
    assert response.status_code == 400


def test_pr_close_removes_all_revisions_and_survives_restart(
    api_client: TestClient, tmp_path: Path
) -> None:
    first = upload(api_client)
    second = upload(api_client, cli="catr", sha="b" * 40)
    unrelated = upload(api_client, pr=456)
    path = "/api/pull-requests/123"
    assert api_client.delete(path).status_code == 401
    response = api_client.delete(path, headers={"Authorization": "Bearer test-deployment-token"})
    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
    assert not (tmp_path / "data" / first["id"]).exists()
    assert not (tmp_path / "data" / second["id"]).exists()
    assert api_client.get(f"/api/environments/{unrelated['id']}").status_code == 200
    restarted = create_app(
        Settings(
            data_dir=tmp_path / "data",
            public_url="https://play.example",
            deploy_token="test-deployment-token",
            sandbox_image="sandbox",
        )
    )
    with TestClient(restarted, base_url="https://play.example") as client:
        response = client.post(
            "/api/environments?sha=" + "a" * 40 + "&pr=123",
            content=binary_bundle({"echor": elf_binary()}),
            headers={"Authorization": "Bearer test-deployment-token"},
        )
        assert response.status_code == 409
        assert client.post(path + "/reopen").status_code == 401
        reopened = client.post(
            path + "/reopen", headers={"Authorization": "Bearer test-deployment-token"}
        )
        assert reopened.status_code == 200
        assert reopened.json() == {"reopened": 123}
        assert upload(client)["id"] == first["id"]


def test_pr_close_rejects_an_upload_that_started_before_closure(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_url="https://play.example",
            deploy_token="test-deployment-token",
            sandbox_image="sandbox",
        )
    )

    async def exercise() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def body() -> AsyncIterator[bytes]:
            yield binary_bundle({"echor": elf_binary()})
            started.set()
            await release.wait()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://play.example",
            headers={"Authorization": "Bearer test-deployment-token"},
        ) as client:
            deploying = asyncio.create_task(
                client.post("/api/environments?sha=" + "a" * 40 + "&pr=123", content=body())
            )
            await started.wait()
            closing = asyncio.create_task(client.delete("/api/pull-requests/123"))
            for _ in range(100):
                if app.state.store.is_closed(123):
                    break
                await asyncio.sleep(0.001)
            assert app.state.store.is_closed(123)
            release.set()
            assert (await deploying).status_code == 409
            assert (await closing).json() == {"deleted": 0}
            assert not list(tmp_path.glob("*/metadata.json"))

    asyncio.run(exercise())


def test_invalid_batch_does_not_replace_the_live_revision(api_client: TestClient) -> None:
    deployed = upload(api_client)
    response = api_client.post(
        "/api/environments",
        params={"sha": "b" * 40, "pr": 123},
        content=binary_bundle({"catr": elf_binary(), "echor": b"invalid"}),
        headers={"Authorization": "Bearer test-deployment-token"},
    )
    assert response.status_code == 422
    assert api_client.get(f"/api/environments/{deployed['id']}").json()["sha"] == "a" * 40


def test_startup_removes_abandoned_uploads_but_keeps_published_batches(
    api_client: TestClient, tmp_path: Path
) -> None:
    deployed = upload(api_client)
    data_dir = tmp_path / "data"
    abandoned = data_dir / ".upload-abandoned"
    abandoned.mkdir()
    (abandoned / "bundle.zip").write_bytes(b"unfinished")
    assert api_client.get(f"/api/environments/{deployed['id']}").status_code == 200
    assert abandoned.exists()
    settings = Settings(
        data_dir=data_dir,
        public_url="https://play.example",
        deploy_token="secret",
        sandbox_image="sandbox",
    )
    with TestClient(create_app(settings), base_url=settings.public_url) as restarted:
        assert not abandoned.exists()
        assert restarted.get(f"/api/environments/{deployed['id']}").status_code == 200
