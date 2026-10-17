"""One assembled production-manifest scenario, with optional monitoring and capacity."""

import asyncio
import json
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from boxie.persistence.models import Sandbox
from boxie_infra.smoke import exercise as generic_smoke
from boxie_infra.terminal import Session
from boxie_test_support.orchestrator import bundle
from boxie_test_support.terminal_network import attach
from sqlmodel import col, select
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from integration.deployment import DeploymentStack
from integration.test_lifecycle import accounting, create, publish, until

pytestmark = pytest.mark.integration


@pytest.fixture(
    scope="module",
    params=[True] if os.environ.get("BOXIE_DEPLOYMENT_CAPACITY") == "1" else [False, True],
)
def assembled_stack(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Iterator[DeploymentStack]:
    stack = DeploymentStack(
        tmp_path_factory.mktemp("boxie-assembled"), monitoring=bool(request.param)
    )
    try:
        stack.start()
        yield stack
    finally:
        stack.close()


def service_ids(stack: DeploymentStack) -> dict[str, str]:
    result = {role: stack.runtime_command("ps", "-q", role) for role in ("api", "orchestrator")}
    result.update(
        {
            unit: stack.compose(unit, "ps", "-q", "postgres" if unit == "postgres" else "temporal")
            for unit in ("postgres", "temporal")
        }
    )
    if stack.monitoring:
        result["monitoring"] = " ".join(sorted(stack.monitoring_command("ps", "-q").split()))
    assert all(result.values())
    return result


async def terminal_echo(socket: ClientConnection, epoch: int) -> None:
    marker = uuid4().hex
    session = Session(socket.recv, socket.send)
    session.epoch = epoch
    await session.write(f"printf '%s%s\\n' '{marker[:16]}' '{marker[16:]}'\n".encode())
    output = b""
    async with asyncio.timeout(15):
        while marker.encode() not in output:
            output += await session.read()
            assert len(output) < 65536


def test_fresh_install_independent_release_failure_and_profile_ack(
    assembled_stack: DeploymentStack,
) -> None:
    async def scenario() -> None:
        stack = assembled_stack
        assert set(stack.supporting_releases) == (
            {"postgres", "temporal", "observability"}
            if stack.monitoring
            else {"postgres", "temporal"}
        )
        assert all(record["containers_preserved"] for record in stack.supporting_releases.values())
        assert "playground" not in stack.bundles
        assert not any(
            key.startswith("PLAYGROUND_") for key in stack.bundles["boxie"]["release"]["settings"]
        )
        await generic_smoke(stack.api_url, stack.root / "consumer.secret", "smoke")
        headers = {"Authorization": "Bearer " + stack.consumer}
        async with httpx.AsyncClient(base_url=stack.api_url, headers=headers, timeout=30) as api:
            artifact = await publish(api)
            pinned = await create(api, artifact, uuid4().hex)
            await until(api, pinned, "ready")
            statement = select(Sandbox.snapshot).where(col(Sandbox.id) == UUID(pinned))
            snapshot = (await stack.rows(statement))[0]
            initial_ids = await asyncio.to_thread(service_ids, stack)
            await asyncio.to_thread(stack.add_playground)
            assert await asyncio.to_thread(service_ids, stack) == initial_ids
            assert (await stack.rows(statement))[0] == snapshot
            await api.delete(f"/v1/sandboxes/{pinned}")
            await until(api, pinned, "terminated")
            await accounting(stack, pinned, 0)
            token = uuid4().hex
            sentinel = Path(stack.env["BOXIE_DATA_DIR"]) / "retained-sentinel"
            await asyncio.to_thread(
                stack.runtime_command,
                "exec",
                "-T",
                "orchestrator",
                "python",
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])",
                str(sentinel),
                token,
            )
            assert sentinel.stat().st_uid == 10001
            if stack.monitoring:
                await asyncio.to_thread(
                    stack.monitoring_command,
                    "exec",
                    "-T",
                    "grafana",
                    "sh",
                    "-c",
                    'printf %s "$1" > /var/lib/grafana/retained-sentinel',
                    "fixture",
                    token,
                )
            product_before = await asyncio.to_thread(
                stack.application_command, "playground", "ps", "-q", "gateway"
            )
            await asyncio.to_thread(stack.release, "playground")
            assert (
                await asyncio.to_thread(
                    stack.application_command, "playground", "ps", "-q", "gateway"
                )
                != product_before
            )
            result = await asyncio.to_thread(stack.release, "playground", failed=True)
            assert result["outcome"] == "failed-rolled-back"
            assert await asyncio.to_thread(service_ids, stack) == initial_ids
            assert sentinel.read_text() == token
            for role in ("api", "orchestrator"):
                info = json.loads(
                    await asyncio.to_thread(stack.command, ["docker", "inspect", initial_ids[role]])
                )[0]
                assert info["Config"]["User"] == "10001:10001"
                assert bool(info["HostConfig"].get("ReadonlyRootfs"))
            if stack.monitoring:
                from boxie_infra.operations import UNITS, native_readiness
                from deploy_tool.remote import Activator

                owner = stack.root.parents[
                    0
                ]  # Probes inspect actual container addresses, not host ports.
                compose = ["docker", "compose", "-p", stack.name + "-monitoring"]

                # The native owner helper only uses ps/inspect here; reuse the
                # configured command boundary rather than re-rendering assets.
                def run(argv: list[str], env: dict[str, str], cwd: Path, seconds: float) -> str:
                    if argv[: len(compose)] == compose:
                        return stack.monitoring_command(*argv[len(compose) :])
                    return Activator(stack.root, 60, 30).run(argv, env, cwd, seconds)

                await asyncio.to_thread(
                    native_readiness,
                    "observability",
                    compose,
                    UNITS["observability"],
                    stack.env,
                    owner,
                    run,
                )
                assert (stack.root / "volumes/grafana/retained-sentinel").read_text() == token
            product_headers = {"Authorization": "Bearer " + stack.deploy}
            async with httpx.AsyncClient(
                base_url=stack.product_url, headers=product_headers, timeout=30
            ) as product:
                state = (await product.get("/api/pull-requests/123/lifecycle")).json()
                response = await product.post(
                    "/api/environments",
                    params={"sha": "a" * 40, "pr": 123, "expected_generation": state["generation"]},
                    content=bundle({"echor": stack.binary.read_bytes()}),
                )
                assert response.status_code == 201, response.text
                identifier = response.json()["id"]
                admission = await product.post(
                    f"/api/environments/{identifier}/sessions",
                    headers={"Origin": stack.product_url},
                    json={"revision": "a" * 40},
                )
                assert admission.status_code == 201, admission.text
                session = admission.json()
                socket_url = (
                    stack.product_url.replace("http:", "ws:")
                    + f"/ws/{identifier}?session_id={session['id']}&revision="
                    + "a" * 40
                )
                async with connect(
                    socket_url, origin=Origin(stack.product_url), proxy=None
                ) as socket:
                    await socket.send(
                        json.dumps({"type": "auth", "credential": session["credential"]})
                    )
                    assert json.loads(await socket.recv())["type"] == "authenticated"
                    epoch, _ = await attach(socket)
                    await terminal_echo(socket, epoch)
                    binding = json.loads(
                        (
                            stack.root / "product/.operations/bindings" / f"{session['id']}.json"
                        ).read_bytes()
                    )
                    sandbox = binding["sandbox"]["id"]
                    await asyncio.to_thread(stack.release, "boxie")
                    async with asyncio.timeout(30):
                        try:
                            while True:
                                frame = await socket.recv()
                                if (
                                    isinstance(frame, str)
                                    and json.loads(frame).get("type") == "status"
                                ):
                                    break
                        except ConnectionClosed:
                            pass
                    await until(api, sandbox, "terminated")
                    await accounting(stack, sandbox, 0)
                await asyncio.to_thread(stack.process, "temporal", "kill")
                async with asyncio.timeout(45):
                    while (await api.get("/readyz")).status_code != 503:
                        await asyncio.sleep(0.2)
                await asyncio.to_thread(stack.process, "temporal", "start")
                await asyncio.to_thread(stack.wait_ready)
                assert (await api.get("/readyz")).status_code == 200
            await api.delete(f"/v1/artifacts/{artifact}")
        stack.evidence["assembled_scenario"] = {
            "independent_updates": True,
            "failed_rollback": True,
            "profile_ack_old_snapshot": True,
            "terminal_ended_on_boxie_update": True,
            "dependency_recovered": True,
        }
        (stack.root / "evidence.json").write_text(json.dumps(stack.evidence, indent=2))

    asyncio.run(scenario())


def capacity_sample(stack: DeploymentStack) -> dict[str, Any]:
    containers = stack.children()
    assert len(containers) == 8
    sample: dict[str, Any] = {
        "elapsed_monotonic": time.monotonic(),
        "load": os.getloadavg(),
        "memory": {
            line.split(":")[0]: line.split(":")[1].strip()
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith(("MemTotal:", "MemAvailable:"))
        },
        "disk": dict(zip(("total", "used", "free"), shutil.disk_usage(stack.root), strict=True)),
        "pressure": {
            name: Path("/proc/pressure", name).read_text()[:4096]
            for name in ("cpu", "memory", "io")
        },
        "containers": [],
    }
    for container in containers:
        info = json.loads(stack.command(["docker", "inspect", container]))[0]
        assert not info["State"]["OOMKilled"] and info["State"]["Running"]
        cgroup = next(
            line.split(":", 2)[2]
            for line in Path(f"/proc/{info['State']['Pid']}/cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
        root = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
        sample["containers"].append(
            {
                "id": container,
                **{
                    name: (root / name).read_text()[:4096]
                    for name in (
                        "memory.events",
                        "memory.current",
                        "memory.max",
                        "cpu.stat",
                        "cpu.max",
                        "pids.current",
                        "memory.pressure",
                        "cpu.pressure",
                    )
                },
            }
        )
    metrics = (
        stack.runtime_command(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "import json,urllib.request; data=urllib.request.urlopen('http://127.0.0.1:9101/metrics',timeout=2).read(1048576).decode(); print(json.dumps([line for line in data.splitlines() if line.startswith(('boxie_log_dropped_total','boxie_log_sink_available','boxie_snapshot_fresh'))]))",
        )
        if stack.monitoring
        else "[]"
    )
    sample["telemetry"] = json.loads(metrics)
    return sample


@pytest.mark.skipif(
    os.environ.get("BOXIE_DEPLOYMENT_CAPACITY") != "1",
    reason="select --capacity for the 120-second eight-slot observation",
)
def test_eight_sandbox_capacity_observation(assembled_stack: DeploymentStack) -> None:

    async def observe() -> None:
        stack = assembled_stack
        async with httpx.AsyncClient(
            base_url=stack.api_url,
            headers={"Authorization": "Bearer " + stack.consumer},
            timeout=30,
        ) as client:
            artifact = await publish(client)
            identifiers: list[str] = []
            samples: list[dict[str, Any]] = []
            try:
                async with AsyncExitStack() as contexts:
                    sockets = []
                    for _ in range(8):
                        identifier = await create(client, artifact, uuid4().hex)
                        identifiers.append(identifier)
                        await until(client, identifier, "ready")
                        socket = await contexts.enter_async_context(
                            connect(
                                stack.api_url.replace("http:", "ws:")
                                + f"/v1/sandboxes/{identifier}/terminal",
                                additional_headers={"Authorization": "Bearer " + stack.consumer},
                                proxy=None,
                            )
                        )
                        epoch, _ = await attach(socket, order=1)
                        sockets.append((socket, epoch))
                    start = time.monotonic()
                    while time.monotonic() - start < 120:
                        for socket, epoch in sockets:
                            await terminal_echo(socket, epoch)
                            await socket.send(
                                json.dumps(
                                    {"type": "heartbeat", "epoch": epoch, "nonce": len(samples)}
                                )
                            )
                            async with asyncio.timeout(5):
                                while True:
                                    frame = await socket.recv()
                                    if isinstance(frame, bytes):
                                        await socket.send(
                                            json.dumps(
                                                {
                                                    "type": "ack",
                                                    "epoch": epoch,
                                                    "sequence": int.from_bytes(frame[:8], "big"),
                                                }
                                            )
                                        )
                                    if (
                                        isinstance(frame, str)
                                        and json.loads(frame).get("type") == "heartbeat"
                                    ):
                                        break
                        samples.append(await asyncio.to_thread(capacity_sample, stack))
                        await asyncio.sleep(min(10, max(0, 120 - (time.monotonic() - start))))
                    stack.evidence["capacity"] = {
                        "duration_seconds": time.monotonic() - start,
                        "samples": samples,
                        "limits_changed": False,
                        "production_vm_sizing_proven": False,
                    }
            finally:
                for identifier in identifiers:
                    await client.delete(f"/v1/sandboxes/{identifier}")
                    await until(client, identifier, "terminated")
                    await accounting(stack, identifier, 0)
                await client.delete(f"/v1/artifacts/{artifact}")
                (stack.root / "evidence.json").write_text(json.dumps(stack.evidence, indent=2))

    asyncio.run(observe())
