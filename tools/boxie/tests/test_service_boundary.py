import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from boxie_test_support.agent import Clock, DockerDaemon, bundle, configuration, live, request
from boxie_test_support.api import CONSUMER_AUTH, api_server, artifact, create, ready, terminal_url
from websockets.asyncio.client import connect


def test_api_restart_preserves_agent_resource_capacity_and_draining(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path, max_sandboxes=1), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (control, agent):
            async with api_server(str(control.base_url)) as client:
                artifact_id = await artifact(client)
                identifier = await create(client, artifact_id)
                await ready(client, identifier)
                ws = await connect(
                    terminal_url(client, identifier), additional_headers=CONSUMER_AUTH
                )
                assert await ws.recv() == b"ready\n"
                readiness = await client.get("/readyz")
                assert readiness.status_code == 200
                assert readiness.json() == {
                    "release": "test-release",
                    "dependencies": {"agent": True},
                }
            await ws.wait_closed()
            resource = next(iter(daemon.configs))
            terminal = daemon.terminals[resource]
            terminal.queue.put_nowait(b"output while API is down")
            await asyncio.sleep(0.03)
            assert terminal.queue.empty() and not terminal.closed
            async with api_server(str(control.base_url)) as restarted:
                assert await create(restarted, artifact_id) == identifier
                assert (await ready(restarted, identifier))["state"] == "ready"
                assert (
                    await restarted.post(
                        "/v1/sandboxes",
                        json=request(artifact_id)["spec"],
                        headers={"Idempotency-Key": "other"},
                    )
                ).status_code == 429
            assert list(daemon.configs) == [resource] and agent.reserved_count == 1

    asyncio.run(exercise())


def test_cancelled_upload_cleans_both_proxy_hops(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        config = configuration(tmp_path)
        async with live(config, DockerDaemon(), Clock()) as (control, _agent):
            async with api_server(str(control.base_url)) as client:
                identifier = str(uuid4())
                started, release = asyncio.Event(), asyncio.Event()
                data = bundle()

                async def interrupted() -> AsyncIterator[bytes]:
                    yield data[:16]
                    started.set()
                    await release.wait()
                    yield data[16:]

                task = asyncio.create_task(
                    client.put(
                        f"/v1/artifacts/{identifier}",
                        content=interrupted(),
                        headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
                    )
                )
                await started.wait()
                for _ in range(100):
                    if list((config.data_dir / "artifacts").glob(".upload-*")):
                        break
                    await asyncio.sleep(0.01)
                assert list((config.data_dir / "artifacts").glob(".upload-*"))
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                for _ in range(100):
                    if not list((config.data_dir / "artifacts").glob(".upload-*")):
                        break
                    await asyncio.sleep(0.01)
                assert not list((config.data_dir / "artifacts").glob(".upload-*"))
                response = await client.put(
                    f"/v1/artifacts/{identifier}",
                    content=data,
                    headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
                )
                assert response.status_code == 201
        assert not [record for record in caplog.records if record.levelname == "ERROR"]

    asyncio.run(exercise())


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
                "agent_url": "http://127.0.0.1:8091",
                "agent_credential_file": str(config.credential_file),
                "consumer_credential_files": {"one": str(consumer)},
                "release": "process-release",
            }
        )
    )
    with pytest.raises(ValueError, match="durable"):
        load_configuration(path)


def test_dependency_failure_is_bounded_and_readiness_reports_release() -> None:
    async def exercise() -> None:
        async with api_server("http://127.0.0.1:1") as client:
            assert (await client.get("/healthz", headers={"Authorization": ""})).status_code == 200
            readiness = await client.get("/readyz")
            assert readiness.status_code == 503
            assert readiness.json() == {"release": "test-release", "dependencies": {"agent": False}}
            response = await client.get(f"/v1/sandboxes/{uuid4()}")
            assert response.status_code == 503
            assert "secret" not in response.text and "127.0.0.1" not in response.text

    asyncio.run(exercise())


def test_api_configuration_rejects_reused_or_public_secret_files(tmp_path: Path) -> None:
    from boxie.bootstrap.api import load_configuration

    secret = tmp_path / "secret"
    secret.write_text("agent-test-secret")
    secret.chmod(0o600)
    path = tmp_path / "api.json"
    path.write_text(
        json.dumps(
            {
                "agent_url": "http://127.0.0.1:8091",
                "agent_credential_file": str(secret),
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


def test_agent_client_control_protocol_and_bounded_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import UUID

    from boxie.domain.errors import BoxieError
    from boxie.domain.models import Principal, TerminationReason
    from boxie.sandbox.client import AgentClient

    async def exercise() -> None:
        config, daemon = configuration(tmp_path, docker_timeout=0.05), DockerDaemon()
        async with live(config, daemon, Clock()) as (control, _agent):
            async with api_server(str(control.base_url)) as client:
                artifact_id = UUID(await artifact(client))
                identifier = UUID(await create(client, str(artifact_id)))
                view = await ready(client, str(identifier))
                async with AgentClient(
                    str(control.base_url), b"agent-test-secret", timeout=0.5
                ) as network:
                    principal = Principal("one")
                    snapshot = await network.resolve_profile(
                        "shell", ("/bin/sh",), {"TERM": "xterm-256color"}
                    )
                    assert snapshot.image_digest.endswith("1" * 64)
                    inspected = await network.inspect(principal, config.identity, identifier, 1)
                    assert inspected.id == identifier and inspected.state == "ready"
                    ensured = await network.ensure(
                        principal,
                        config.identity,
                        identifier,
                        1,
                        snapshot,
                        artifact_id,
                        inspected.absolute_deadline,
                    )
                    assert ensured.id == identifier and len(daemon.configs) == 1
                    with pytest.raises(BoxieError) as foreign:
                        await network.inspect(Principal("two"), config.identity, identifier, 1)
                    assert foreign.value.status == 404
                    assert view["cleanup_pending"] is False
                    assert await network.ready()

                    async def blocked() -> bool:
                        await asyncio.Event().wait()
                        return True

                    monkeypatch.setattr(daemon, "ready", blocked)
                    async with asyncio.timeout(0.4):
                        readiness = await client.get("/readyz")
                    assert readiness.status_code == 503
                    assert readiness.json()["dependencies"] == {"agent": False}
                    result = await network.remove(
                        principal, config.identity, identifier, 1, TerminationReason.REQUESTED
                    )
                    assert result.state == "terminated" and not daemon.configs

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "frame",
    [
        '{"type":"status","reason":"agent-test-secret"}',
        '{"type":"status","reason":"requested","secret":"agent-test-secret"}',
        "not json",
    ],
)
def test_agent_client_rejects_malformed_status_without_leaking_payload(frame: str) -> None:
    from boxie.domain.errors import BoxieError
    from boxie.domain.models import Principal
    from boxie.sandbox.client import AgentClient
    from boxie_test_support.api import serve
    from fastapi import FastAPI, WebSocket

    async def exercise() -> None:
        # A deliberately malformed peer characterizes the network adapter, while
        # the E2E suite above always runs the real agent and replaces Docker only.
        peer = FastAPI()

        @peer.websocket("/v1/sandboxes/{identifier}/terminal")
        async def malformed(websocket: WebSocket, identifier: str) -> None:
            await websocket.accept()
            await websocket.send_text(frame)
            await websocket.close()

        async with serve(peer) as url:
            async with AgentClient(url, b"agent-test-secret") as client:
                with pytest.raises(BoxieError) as error:
                    async with client.attach(Principal("one"), uuid4()) as terminal:
                        await terminal.receive()
                assert error.value.status == 503 and "agent-test-secret" not in str(error.value)

    asyncio.run(exercise())
