import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from boxie_test_support.orchestrator import configuration


def test_standalone_api_requires_durable_configuration(tmp_path: Path) -> None:
    from boxie.bootstrap.api import load_configuration

    config = configuration(tmp_path)
    consumer = tmp_path / "consumer.secret"
    consumer.write_text("consumer-one-secret")
    consumer.chmod(0o600)
    path = tmp_path / "api.json"
    path.write_text(
        json.dumps(
            {
                "orchestrator_url": "http://127.0.0.1:8091",
                "orchestrator_credential_file": str(config.credential_file),
                "consumer_credential_files": {"one": str(consumer)},
                "release": "process-release",
            }
        )
    )
    with pytest.raises(ValueError, match="durable"):
        load_configuration(path)


def test_api_configuration_rejects_reused_or_public_secret_files(tmp_path: Path) -> None:
    from boxie.bootstrap.api import load_configuration

    secret = tmp_path / "secret"
    secret.write_text("orchestrator-test-secret")
    secret.chmod(0o600)
    path = tmp_path / "api.json"
    path.write_text(
        json.dumps(
            {
                "orchestrator_url": "http://127.0.0.1:8091",
                "orchestrator_credential_file": str(secret),
                "consumer_credential_files": {"one": str(secret)},
                "release": "test",
            }
        )
    )
    with pytest.raises(ValueError, match="distinct"):
        load_configuration(path)
    secret.chmod(0o644)
    with pytest.raises(ValueError, match="private regular"):
        load_configuration(path)


@pytest.mark.parametrize(
    "frame",
    [
        '{"type":"status","reason":"orchestrator-test-secret"}',
        '{"type":"status","reason":"requested","secret":"orchestrator-test-secret"}',
        "not json",
    ],
)
def test_orchestrator_client_rejects_malformed_status_without_leaking_payload(frame: str) -> None:
    from boxie.domain.errors import BoxieError
    from boxie.domain.models import Principal
    from boxie.sandbox.client import OrchestratorClient
    from boxie_test_support.api import serve
    from fastapi import FastAPI, WebSocket

    async def exercise() -> None:
        # A deliberately malformed peer characterizes the network adapter, while
        # the E2E suite above always runs the real orchestrator and replaces Docker only.
        peer = FastAPI()

        @peer.websocket("/v1/sandboxes/{identifier}/terminal")
        async def malformed(websocket: WebSocket, identifier: str) -> None:
            await websocket.accept()
            await websocket.send_text(frame)
            await websocket.close()

        async with serve(peer) as url:
            async with OrchestratorClient(url, b"orchestrator-test-secret") as client:
                with pytest.raises(BoxieError) as error:
                    async with client.attach(Principal("one"), uuid4()) as terminal:
                        await terminal.receive()
                assert error.value.status == 503 and "orchestrator-test-secret" not in str(
                    error.value
                )

    asyncio.run(exercise())
