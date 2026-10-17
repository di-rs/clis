"""Publication generation fences close/reopen even when revision and SHA are unchanged."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from playground.storage import ArtifactConflictError, EnvironmentStore

from .helpers import AUTH, Settings, binary_bundle, elf_binary
from .test_service import make_service


def test_lifecycle_invalidates_transfer_before_intent_creation(docker_settings: Settings) -> None:
    async def exercise() -> None:
        service = make_service(docker_settings)
        entered, release = asyncio.Event(), asyncio.Event()

        async def chunks() -> AsyncIterator[bytes]:
            entered.set()
            await release.wait()
            yield binary_bundle({"echor": elf_binary()})

        transfer = asyncio.create_task(
            service.deploy(chunks(), "a" * 40, 123, expected_generation=0)
        )
        await entered.wait()
        close = asyncio.create_task(service.close_pull_request(123))
        reopen = asyncio.create_task(service.reopen_pull_request(123))
        await asyncio.sleep(0)
        assert service.store.lifecycle(123).generation == 2
        assert service.store.lifecycle(123).closed
        release.set()
        with pytest.raises(ArtifactConflictError, match="stale lifecycle"):
            await transfer
        await close
        await reopen
        assert service.store.lifecycle(123).generation == 2
        assert not service.store.lifecycle(123).closed
        assert service.store.get(service.store.identifier(123)) is None
        assert service.sessions.uploads() == []
        restarted = EnvironmentStore(docker_settings)
        assert restarted.lifecycle(123).generation == 2

    asyncio.run(exercise())


def test_api_requires_captured_generation_and_acknowledges_it(api_client: TestClient) -> None:
    path = "/api/pull-requests/123/lifecycle"
    assert api_client.get(path).status_code == 401
    assert api_client.get(path, headers=AUTH).json() == {"generation": 0, "closed": False}
    assert (
        api_client.post(
            "/api/environments", params={"sha": "a" * 40, "pr": 123}, headers=AUTH
        ).status_code
        == 422
    )
    assert api_client.delete("/api/pull-requests/123", headers=AUTH).status_code == 200
    assert api_client.post("/api/pull-requests/123/reopen", headers=AUTH).status_code == 200
    params = {"sha": "a" * 40, "pr": 123, "expected_generation": 0}
    response = api_client.post(
        "/api/environments",
        params=params,
        headers=AUTH,
        content=binary_bundle({"echor": elf_binary()}),
    )
    assert response.status_code == 409 and response.json()["detail"] == "stale lifecycle"
    params["expected_generation"] = 2
    response = api_client.post(
        "/api/environments",
        params=params,
        headers=AUTH,
        content=binary_bundle({"echor": elf_binary()}),
    )
    assert response.status_code == 201, response.text
    assert response.json()["expected_generation"] == 2


def test_corrupt_lifecycle_state_fails_closed(docker_settings: Settings) -> None:
    store = EnvironmentStore(docker_settings)
    store.close_pull_request(123)
    (store.root / ".lifecycle" / "123.json").write_text("corrupt")
    with pytest.raises(ValueError):
        store.lifecycle(123)


def test_generation_invalidated_during_boxie_upload_never_commits(
    docker_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        service = make_service(docker_settings)
        entered, release = asyncio.Event(), asyncio.Event()
        original = service.boxie.upload

        from pathlib import Path
        from uuid import UUID

        async def paused(identifier: UUID, digest: str, archive: Path) -> None:
            await original(identifier, digest, archive)
            entered.set()
            await release.wait()

        monkeypatch.setattr(service.boxie, "upload", paused)

        async def chunks() -> AsyncIterator[bytes]:
            yield binary_bundle({"echor": elf_binary()})

        transfer = asyncio.create_task(
            service.deploy(chunks(), "a" * 40, 123, expected_generation=0)
        )
        await entered.wait()
        close = asyncio.create_task(service.close_pull_request(123))
        reopen = asyncio.create_task(service.reopen_pull_request(123))
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(ArtifactConflictError, match="stale lifecycle"):
            await transfer
        await close
        await reopen
        assert service.sessions.uploads() == []
        assert service.store.get(service.store.identifier(123)) is None

    asyncio.run(exercise())


def test_data_directory_rejects_a_second_writer_until_lifespan_ends(api_client: TestClient) -> None:
    from fastapi import FastAPI
    from playground.app import create_app

    from .boxie_fake import FakeBoxie

    assert isinstance(api_client.app, FastAPI)
    settings = api_client.app.state.settings
    with pytest.raises(RuntimeError, match="another Playground writer"):
        with TestClient(create_app(settings, boxie=FakeBoxie())):
            pytest.fail("a second writer served the same directory")
    api_client.__exit__(None, None, None)
    with TestClient(
        create_app(settings, boxie=FakeBoxie()), base_url=settings.public_url
    ) as restarted:
        assert restarted.get("/healthz").status_code == 200


@pytest.mark.parametrize("fault", ["retry", "ack"])
def test_publisher_keeps_one_generation_and_rejects_wrong_ack(fault: str) -> None:
    import urllib.error

    from playground.preview.publication import publish

    from .test_preview_ci import REPO, FakeGitHub

    github = FakeGitHub()
    generation = 0
    transfers: list[str] = []

    def gateway(
        base: str,
        token: str,
        path: str,
        *,
        method: str,
        data: bytes | None = None,
        content_type: str = "application/zip",
    ) -> dict[str, object]:
        nonlocal generation
        if path.endswith("/lifecycle"):
            return {"generation": generation, "closed": False}
        if path.startswith("/api/environments?"):
            transfers.append(path)
            if fault == "retry":
                generation = 2
                raise urllib.error.HTTPError(path, 429, "busy", {}, None)  # type: ignore[arg-type]
            return {
                "id": "a" * 32,
                "url": "https://play.example/p/" + "a" * 32,
                "expected_generation": 2,
            }
        raise AssertionError("stale transfer attempted a second write")

    assert (
        publish(
            github,
            REPO,
            500,
            "https://play.example",
            "token",
            gateway=gateway,
            sleep=lambda delay: None,
        )
        is None
    )
    assert len(transfers) == 1 and "expected_generation=0" in transfers[0]
    assert github.writes == []
