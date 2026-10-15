"""Agent behavior across actual authenticated HTTP and WebSocket transports."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from boxie_test_support import (
    AUTH,
    IMAGE,
    Clock,
    DockerDaemon,
    configuration,
    live,
    request,
    settled,
    upload,
)
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


def test_generic_shell_idempotency_isolation_and_terminal(tmp_path: Path) -> None:
    async def exercise() -> None:
        daemon, clock = DockerDaemon(), Clock()
        config = configuration(tmp_path)
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            first = await client.post("/v1/sandboxes", json=request(artifact))
            assert first.status_code == 200, first.text
            identifier = first.json()["id"]
            await settled(client, identifier, "ready")
            repeated = await client.post("/v1/sandboxes", json=request(artifact))
            assert repeated.json()["id"] == identifier
            assert len(daemon.configs) == 1
            assert daemon.events == ["create", "attach", "start"]
            payload = next(iter(daemon.configs.values()))
            assert payload["Image"] == IMAGE
            assert payload["User"] == "65534:65534"
            host = payload["HostConfig"]
            assert host["Runtime"] == "runsc"
            assert host["NetworkMode"] == "none"
            assert host["ReadonlyRootfs"] is True
            assert host["CapDrop"] == ["ALL"]
            assert host["SecurityOpt"] == ["no-new-privileges"]
            assert host["Memory"] == host["MemorySwap"] == 536870912
            assert host["NanoCpus"] == 1000000000
            assert host["PidsLimit"] == 256
            assert host["Ulimits"] == [{"Name": "nofile", "Soft": 1024, "Hard": 1024}]
            assert "size=67108864" in host["Tmpfs"]["/workspace"]
            assert "size=16777216" in host["Tmpfs"]["/tmp"]
            mount = host["Mounts"][0]
            assert mount["Target"] == "/opt/artifact" and mount["ReadOnly"] is True
            assert (Path(mount["Source"]) / "nested/hello.txt").read_bytes() == b"ordinary text\n"
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                for data in (b"\xc3", b"\xa9\n"):
                    await ws.send(data)
                    assert await ws.recv() == data
                await ws.send('{"type":"resize","columns":100,"rows":30}')
                await asyncio.sleep(0.02)
                assert next(iter(daemon.terminals.values())).sizes[-1] == (100, 30)
            await asyncio.sleep(0.03)
            assert agent.reserved_count == 1
            clock.advance(120)
            done = await settled(client, identifier, "terminated")
            assert done["reason"] == "disconnected"

    asyncio.run(exercise())


def test_auth_ownership_validation_and_capacity(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path, max_sandboxes=1), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            denied = await client.post(
                "/v1/sandboxes", json={}, headers={"Authorization": "Bearer wrong"}
            )
            assert denied.status_code == 401
            artifact = await upload(client)
            for patch in (
                {"image": "evil"},
                {"host_path": "/etc"},
                {"command": ["/bin/evil"]},
                {"env": {"LD_PRELOAD": "evil"}},
                {"policy": {"max_seconds": 3601}},
            ):
                body = request(artifact)
                body["spec"].update(patch)
                result = await client.post("/v1/sandboxes", json=body)
                assert result.status_code == 422, result.text
            first = await client.post("/v1/sandboxes", json=request(artifact))
            identifier = first.json()["id"]
            await settled(client, identifier, "ready")
            changed = request(artifact)
            changed["spec"]["env"] = {"GREETING": "hello"}
            assert (await client.post("/v1/sandboxes", json=changed)).status_code == 409
            assert (
                await client.post("/v1/sandboxes", json=request(artifact, "second"))
            ).status_code == 429
            for method in (client.get, client.delete):
                assert (
                    await method(f"/v1/sandboxes/{identifier}", params={"consumer_id": "two"})
                ).status_code == 404
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=two"
            )
            with pytest.raises(InvalidStatus):
                async with connect(url, additional_headers=AUTH):
                    pytest.fail("another consumer attached")
            assert (
                await client.delete(f"/v1/artifacts/{artifact}", params={"consumer_id": "two"})
            ).status_code == 404

    asyncio.run(exercise())


def test_cleanup_failure_retains_capacity_and_retired_artifact(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path, max_sandboxes=1), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            first = await client.post("/v1/sandboxes", json=request(artifact))
            identifier = first.json()["id"]
            await settled(client, identifier, "ready")
            daemon.fail_delete = True
            assert (
                await client.delete(f"/v1/artifacts/{artifact}", params={"consumer_id": "one"})
            ).status_code == 204
            deleted = await client.delete(
                f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"}
            )
            assert deleted.json()["cleanup_pending"] is True
            assert deleted.json()["state"] == "terminating"
            assert agent.reserved_count == 1
            assert agent.artifact_exists(artifact)
            daemon.fail_delete = False
            await settled(client, identifier, "terminated")
            assert agent.reserved_count == 0
            assert not agent.artifact_exists(artifact)
            assert (
                await client.post("/v1/sandboxes", json=request(artifact, "new"))
            ).status_code == 409

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("policy", "advance", "reason"),
    [({"max_seconds": 30}, 30, "expired"), ({"idle_seconds": 20}, 20, "idle")],
)
def test_deadlines_without_client_requests(
    tmp_path: Path, policy: dict[str, int], advance: int, reason: str
) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            artifact = await upload(client)
            body = request(artifact)
            body["spec"]["policy"].update(policy)
            identifier = (await client.post("/v1/sandboxes", json=body)).json()["id"]
            await settled(client, identifier, "ready")
            clock.advance(advance)
            await asyncio.sleep(0.05)
            assert daemon.removed
            assert (await settled(client, identifier, "terminated"))["reason"] == reason

    asyncio.run(exercise())


def test_pending_create_cancellation_and_restart_preserve_uncertainty(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        daemon.create_gate = asyncio.Event()
        daemon.fail_delete = True
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await daemon.created.wait()
            await client.delete(f"/v1/artifacts/{artifact}", params={"consumer_id": "one"})
            result = await client.delete(
                f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"}
            )
            assert result.json()["cleanup_pending"]
            assert agent.reserved_count == 1 and agent.artifact_exists(artifact)
        foreign = "foreign-container"
        daemon.configs[foreign] = {"Labels": {"boxie.installation": str(uuid4())}}
        daemon.fail_delete = False
        async with live(config, daemon, clock) as (client, agent):
            await settled(client, identifier, "terminated")
            assert foreign in daemon.configs and foreign not in daemon.removed
            assert agent.reserved_count == 0 and not agent.artifact_exists(artifact)

    asyncio.run(exercise())


def test_failed_start_is_final_only_after_delete(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        daemon.fail_start = True
        daemon.fail_delete = True
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "terminating")
            assert agent.reserved_count == 1
            daemon.fail_delete = False
            result = await settled(client, identifier, "failed")
            assert result["reason"] == "provisioning_failed" and not result["cleanup_pending"]

    asyncio.run(exercise())


def test_frozen_profile_and_control_ownership_across_reload(tmp_path: Path) -> None:
    import json

    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            resolved = await client.post(
                "/v1/profiles/resolve",
                json={
                    "profile_id": "shell",
                    "command": ["/bin/sh"],
                    "env": {"TERM": "xterm-256color"},
                },
            )
            snapshot = resolved.json()
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            admitted = await settled(client, identifier, "ready")
            profiles = json.loads(config.profiles_file.read_text())
            profiles["shell"]["image_digest"] = "example.org/shell@sha256:" + "2" * 64
            config.profiles_file.write_text(json.dumps(profiles))
            agent.profiles.reload()
            control = {
                "consumer_id": "one",
                "identity": {
                    "installation_id": str(config.identity.installation_id),
                    "host_id": "host-1",
                },
                "generation": 1,
            }
            ensure = {
                **control,
                "snapshot": snapshot,
                "artifact_id": artifact,
                "deadline": admitted["absolute_deadline"],
            }
            assert (
                await client.post(f"/v1/sandboxes/{identifier}/ensure", json=ensure)
            ).status_code == 200
            assert len(daemon.configs) == 1
            ensure["snapshot"]["image_digest"] = profiles["shell"]["image_digest"]
            assert (
                await client.post(f"/v1/sandboxes/{identifier}/ensure", json=ensure)
            ).status_code == 422
            for patch in (
                {"consumer_id": "two"},
                {"generation": 2},
                {"identity": {"installation_id": str(uuid4()), "host_id": "host-1"}},
            ):
                assert (
                    await client.post(
                        f"/v1/sandboxes/{identifier}/inspect", json={**control, **patch}
                    )
                ).status_code == 404
            assert (
                await client.post(f"/v1/sandboxes/{uuid4()}/ensure", json=ensure)
            ).status_code == 404

    asyncio.run(exercise())


def test_input_alone_resets_idle_and_never_absolute_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            artifact = await upload(client)
            body = request(artifact)
            body["spec"]["policy"].update({"idle_seconds": 20, "max_seconds": 30})
            identifier = (await client.post("/v1/sandboxes", json=body)).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                clock.advance(15)
                await ws.send(b"input")
                assert await ws.recv() == b"input"
                clock.advance(10)
                await asyncio.sleep(0.03)
                assert not daemon.removed
                clock.advance(5)
                assert (await settled(client, identifier, "terminated"))["reason"] == "expired"

    asyncio.run(exercise())


def test_output_cap_and_status_are_agent_enforced(tmp_path: Path) -> None:
    import json

    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        profiles = json.loads(config.profiles_file.read_text())
        profiles["shell"]["limits"] = {"output_bytes": 10}
        config.profiles_file.write_text(json.dumps(profiles))
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                await ws.send("ééé".encode())
                assert await ws.recv() == "éé".encode()
                assert json.loads(await ws.recv()) == {"type": "status", "reason": "output_limit"}
            assert (await settled(client, identifier, "terminated"))["reason"] == "output_limit"
            assert agent.reserved_count == 0

    asyncio.run(exercise())


def test_api_client_restart_does_not_terminate_agent_session(tmp_path: Path) -> None:
    import httpx

    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            results = await asyncio.gather(
                *(client.post("/v1/sandboxes", json=request(artifact)) for _ in range(8))
            )
            assert all(result.status_code == 200 for result in results)
            identifier = results[0].json()["id"]
            assert {result.json()["id"] for result in results} == {identifier}
            await settled(client, identifier, "ready")
            base_url = client.base_url
            await client.aclose()
            async with httpx.AsyncClient(base_url=base_url, headers=AUTH) as restarted:
                assert (await restarted.post("/v1/sandboxes", json=request(artifact))).json()[
                    "id"
                ] == identifier
                assert (
                    await restarted.get(
                        f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"}
                    )
                ).json()["state"] == "ready"
            assert agent.reserved_count == 1 and not daemon.removed

    asyncio.run(exercise())


def test_unauthenticated_websocket_and_oversized_control_rejected(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            with pytest.raises(InvalidStatus) as error:
                async with connect(url):
                    pytest.fail("missing agent credential accepted")
            assert error.value.response.status_code == 403
            result = await client.post("/v1/sandboxes", content=b" " * 65537)
            assert result.status_code == 413

    asyncio.run(exercise())


def test_approved_command_replaces_image_entrypoint(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            payload = next(iter(daemon.configs.values()))
            assert payload["Entrypoint"] == []
            assert payload["Cmd"] == ["/bin/sh"]

    asyncio.run(exercise())


def test_failed_delete_closes_terminal_without_releasing_reference(tmp_path: Path) -> None:
    import json

    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                daemon.fail_delete = True
                response = await client.delete(
                    f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"}
                )
                assert response.json()["cleanup_pending"]
                assert json.loads(await asyncio.wait_for(ws.recv(), 0.5))["reason"] == "requested"
                assert next(iter(daemon.terminals.values())).closed
                assert agent.reserved_count == 1 and agent.artifact_exists(artifact)
            daemon.fail_delete = False
            await settled(client, identifier, "terminated")

    asyncio.run(exercise())


def test_idle_resize_and_output_do_not_extend_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            artifact = await upload(client)
            body = request(artifact)
            body["spec"]["policy"]["idle_seconds"] = 20
            identifier = (await client.post("/v1/sandboxes", json=body)).json()["id"]
            await settled(client, identifier, "ready")
            url = (
                str(client.base_url).replace("http:", "ws:")
                + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers=AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                clock.advance(15)
                await ws.send('{"type":"resize","columns":100,"rows":30}')
                next(iter(daemon.terminals.values())).queue.put_nowait(b"background output")
                assert await ws.recv() == b"background output"
                clock.advance(5)
                assert (await settled(client, identifier, "terminated"))["reason"] == "idle"

    asyncio.run(exercise())


def test_restart_does_not_delete_wrong_generation_and_keeps_idempotency(tmp_path: Path) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            artifact = await upload(client)
            identifier = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, identifier, "ready")
            name = next(iter(daemon.configs))
            daemon.configs[name]["Labels"]["boxie.generation"] = "2"
        assert not daemon.removed
        async with live(config, daemon, clock) as (client, agent):
            assert agent.reserved_count == 1
            result = await client.post("/v1/sandboxes", json=request(artifact))
            assert result.json()["id"] == identifier and result.json()["cleanup_pending"]
            assert not daemon.removed
            daemon.configs[name]["Labels"]["boxie.generation"] = "1"
            await settled(client, identifier, "terminated")
            assert agent.reserved_count == 0

    asyncio.run(exercise())


def test_docker_adapter_confirms_auto_removal_and_bounds_daemon_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from aiodocker import Docker
    from aiodocker.containers import DockerContainer, DockerContainers
    from aiodocker.exceptions import DockerError
    from boxie.sandbox.docker import DockerBackend

    async def exercise() -> None:
        client = MagicMock(spec=Docker)
        client.containers = MagicMock(spec=DockerContainers)
        container = MagicMock(spec=DockerContainer)
        client.containers.container.return_value = container
        monkeypatch.setattr("boxie.sandbox.docker.Docker", lambda **_kwargs: client)
        backend = DockerBackend(timeout=0.15)
        container.delete.side_effect = [
            DockerError(409, "removal in progress"),
            DockerError(404, "gone"),
        ]
        await backend.delete("owned")
        assert container.delete.await_count == 2
        container.show.side_effect = DockerError(404, "gone")
        assert await backend.labels("owned") is None
        container.delete.side_effect = DockerError(409, "still deleting")
        with pytest.raises(TimeoutError):
            await backend.delete("owned")
        await backend.close()
        assert client.close.await_count == 1

    asyncio.run(exercise())


def test_slow_cleanup_does_not_delay_another_sandbox_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        config, daemon, clock = configuration(tmp_path), DockerDaemon(), Clock()
        async with live(config, daemon, clock) as (client, _agent):
            artifact = await upload(client)
            first = (await client.post("/v1/sandboxes", json=request(artifact))).json()["id"]
            await settled(client, first, "ready")
            second_body = request(artifact, "second")
            second_body["spec"]["policy"]["max_seconds"] = 10
            second = (await client.post("/v1/sandboxes", json=second_body)).json()["id"]
            await settled(client, second, "ready")
            slow_name = next(
                name
                for name, value in daemon.configs.items()
                if value["Labels"]["boxie.sandbox"] == first
            )
            entered, release = asyncio.Event(), asyncio.Event()
            original = daemon.delete

            async def slow_delete(name: str) -> None:
                if name == slow_name:
                    entered.set()
                    await release.wait()
                await original(name)

            monkeypatch.setattr(daemon, "delete", slow_delete)
            removal = asyncio.create_task(
                client.delete(f"/v1/sandboxes/{first}", params={"consumer_id": "one"})
            )
            try:
                await entered.wait()
                clock.advance(10)
                await asyncio.sleep(0.1)
                result = await client.get(f"/v1/sandboxes/{second}", params={"consumer_id": "one"})
                assert result.json()["state"] == "terminated"
            finally:
                release.set()
                await removal

    asyncio.run(exercise())
