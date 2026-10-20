"""Actual network adapter contracts against deliberately malformed peers."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cli_playground.boxie_client import HttpBoxieClient
from cli_playground.sandbox import BoxieUnavailable, SandboxRequest
from fastapi import FastAPI, Request
from pydantic import ValidationError

from .boxie_network import serve


def test_create_sends_only_the_artifact_id() -> None:
    async def exercise() -> None:
        artifact_id, sandbox_id = uuid4(), uuid4()
        now = datetime.now(UTC)
        peer = FastAPI()
        requests: list[bytes] = []

        @peer.post("/v1/sandboxes")
        async def create(request: Request) -> dict[str, object]:
            requests.append(await request.body())
            assert request.headers["Idempotency-Key"] == "create-once"
            return {
                "id": str(sandbox_id),
                "state": "accepted",
                "created_at": now.isoformat(),
                "absolute_deadline": (now + timedelta(hours=1)).isoformat(),
                "reason": None,
                "cleanup_pending": False,
            }

        async with serve(peer) as url:
            client = HttpBoxieClient(url, "private-consumer-secret")
            try:
                result = await client.create("create-once", SandboxRequest(artifact_id=artifact_id))
                assert result.id == sandbox_id
                assert requests == [f'{{"artifact_id":"{artifact_id}"}}'.encode()]
            finally:
                await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", "cli-playground"),
        ("command", ["/bin/sh"]),
        ("env", {"TERM": "xterm"}),
        ("policy", {"max_seconds": 60}),
    ],
)
def test_create_rejects_consumer_environment_configuration(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as error:
        SandboxRequest.model_validate({"artifact_id": str(uuid4()), field: value})
    assert error.value.errors()[0]["type"] == "extra_forbidden"
    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("fault", ["id", "negative_lifetime", "excessive_lifetime"])
def test_sandbox_responses_are_bound_to_request_identity_and_lifetime(
    method: str, fault: str
) -> None:
    async def exercise() -> None:
        identifier = uuid4()
        now = datetime.now(UTC)
        view: dict[str, object] = {
            "id": str(identifier),
            "state": "ready",
            "created_at": now.isoformat(),
            "absolute_deadline": (now + timedelta(hours=1)).isoformat(),
            "reason": None,
            "cleanup_pending": False,
        }
        if fault == "id":
            view["id"] = str(uuid4())
        elif fault == "negative_lifetime":
            view["absolute_deadline"] = (now - timedelta(seconds=1)).isoformat()
        else:
            view["absolute_deadline"] = (now + timedelta(hours=2)).isoformat()
        peer = FastAPI()

        @peer.api_route("/v1/sandboxes/{resource}", methods=["GET", "DELETE"])
        async def reply(resource: str) -> dict[str, object]:
            return view

        async with serve(peer) as url:
            client = HttpBoxieClient(url, "private-consumer-secret")
            try:
                with pytest.raises(BoxieUnavailable, match="invalid sandbox response"):
                    await (
                        client.get(identifier) if method == "GET" else client.terminate(identifier)
                    )
            finally:
                await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", ["id", "digest", "retired", "malformed"])
def test_upload_reply_must_confirm_exact_immutable_artifact(tmp_path: Path, fault: str) -> None:
    async def exercise() -> None:
        identifier = uuid4()
        archive = tmp_path / "artifact.zip"
        archive.write_bytes(b"archive bytes")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        view: dict[str, object] = {"id": str(identifier), "sha256": digest, "retired": False}
        view.update(
            {"id": str(uuid4())}
            if fault == "id"
            else {"sha256": "f" * 64}
            if fault == "digest"
            else {"retired": True}
            if fault == "retired"
            else {"unknown": "private-consumer-secret"}
        )
        peer = FastAPI()

        @peer.put("/v1/artifacts/{resource}")
        async def reply(resource: str) -> dict[str, object]:
            return view

        async with serve(peer) as url:
            client = HttpBoxieClient(url, "private-consumer-secret")
            try:
                with pytest.raises(BoxieUnavailable, match="invalid artifact response") as error:
                    await client.upload(identifier, digest, archive)
                assert "private-consumer-secret" not in str(error.value)
            finally:
                await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("field", ["created_at", "absolute_deadline"])
def test_sandbox_lifetime_cannot_drift_after_acceptance(method: str, field: str) -> None:
    async def exercise() -> None:
        identifier, now = uuid4(), datetime.now(UTC)
        view: dict[str, object] = {
            "id": str(identifier),
            "state": "ready",
            "created_at": now.isoformat(),
            "absolute_deadline": (now + timedelta(minutes=30)).isoformat(),
            "reason": None,
            "cleanup_pending": False,
        }
        peer = FastAPI()

        @peer.api_route("/v1/sandboxes/{resource}", methods=["GET", "DELETE"])
        async def reply(resource: str) -> dict[str, object]:
            return view

        async with serve(peer) as url:
            client = HttpBoxieClient(url, "private-consumer-secret")
            try:
                original = await client.get(identifier)
                view[field] = (getattr(original, field) + timedelta(seconds=1)).isoformat()
                with pytest.raises(BoxieUnavailable, match="invalid sandbox response"):
                    await (
                        client.get(identifier, original)
                        if method == "GET"
                        else client.terminate(identifier, original)
                    )
            finally:
                await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("valid", [True, False])
def test_terminal_adapter_validates_events_before_returning_them(valid: bool) -> None:
    from cli_playground.terminal_protocol import Attached
    from fastapi import WebSocket

    async def exercise() -> None:
        peer = FastAPI()

        @peer.websocket("/v1/sandboxes/{resource}/terminal")
        async def reply(websocket: WebSocket, resource: str) -> None:
            await websocket.accept()
            await websocket.send_text(
                '{"type":"attached","epoch":3}'
                if valid
                else '{"type":"status","reason":"private-consumer-secret"}'
            )
            await websocket.close()

        async with serve(peer) as url:
            client = HttpBoxieClient(url, "private-consumer-secret")
            try:
                async with client.terminal(uuid4()) as connection:
                    if valid:
                        event = await connection.receive()
                        assert isinstance(event, Attached)
                        assert event.epoch == 3
                    else:
                        with pytest.raises(BoxieUnavailable) as error:
                            await connection.receive()
                        assert "private-consumer-secret" not in str(error.value)
            finally:
                await client.close()

    asyncio.run(exercise())
