"""Outbox acceptance/acknowledgment ambiguity on production Temporal and real SQL."""

import asyncio
import importlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from boxie.persistence.models import OrchestratorObservationRecord, Outbox
from boxie_test_support.terminal_network import attach
from database_support import add_check
from integration.conftest import postgres_dsn as postgres_dsn
from integration.conftest import postgres_server as postgres_server
from integration.conftest import temporal_stack as temporal_stack
from sqlalchemy import func, insert, update
from sqlmodel import col, select
from temporalio.client import Client
from test_postgres import OWNER, database, upload
from test_temporal_deployment import tls

pytestmark = pytest.mark.integration


def test_accepted_before_ack_is_redelivered_to_one_run(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    async def exercise() -> None:
        module = importlib.import_module("boxie.temporal.dispatcher")
        from boxie.persistence.outbox import PostgresOutbox

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _config):
            sandbox = await life.submit(OWNER, "once", await upload(artifacts))
            store = PostgresOutbox(sessions)
            dispatcher = module.OutboxDispatcher(store, client, "outbox-" + uuid4().hex)
            event = (await store.claim("first"))[0]
            await dispatcher.deliver(event)  # dispatcher dies here: no acknowledgment
            handle = client.get_workflow_handle(f"boxie/sandbox/{sandbox.id}")
            first = (await handle.describe()).run_id
            async with sessions.begin() as session:
                await session.exec(
                    update(Outbox).values(
                        lease_expires_at=func.clock_timestamp() - timedelta(seconds=1)
                    )
                )
            retried = (await store.claim("replacement"))[0]
            assert not await store.acknowledge(event)
            await dispatcher.deliver(retried)
            assert await store.acknowledge(retried)
            assert (await handle.describe()).run_id == first
            assert await store.claim("third") == []
            await handle.terminate(reason="test fixture cleanup")

    asyncio.run(exercise())


def test_idle_worker_presence_survives_long_poll_then_expires(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    """Production 1.32.0 poller access refreshes at the end of idle long polls."""

    async def exercise() -> None:
        import time
        from datetime import UTC, datetime, timedelta

        from boxie.bootstrap.durable import Dependencies
        from boxie.bootstrap.worker import lifecycle_worker
        from boxie.temporal.contracts import TASK_QUEUE
        from temporal_support import durable_stack
        from temporalio.api.enums.v1 import TaskQueueType
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        kinds = (TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY)

        async def timestamps() -> dict[int, datetime]:
            result: dict[int, datetime] = {}
            for kind in kinds:
                info = await client.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=client.namespace,
                        task_queue=TaskQueue(name=TASK_QUEUE),
                        task_queue_type=kind,
                    ),
                    timeout=timedelta(seconds=5),
                )
                if info.pollers:
                    result[kind] = max(
                        p.last_access_time.ToDatetime(tzinfo=UTC) for p in info.pollers
                    )
            return result

        async with durable_stack(postgres_dsn, tmp_path, client, TASK_QUEUE) as stack:
            activities = stack[5]
            dependencies = Dependencies(activities.orchestrator, client)
            async with lifecycle_worker(client, activities):
                async with asyncio.timeout(10):
                    while len(initial := await timestamps()) != 2 or not await dependencies():
                        await asyncio.sleep(0.1)
                started = time.monotonic()
                largest_age = 0.0
                async with asyncio.timeout(125):
                    while True:
                        current = await timestamps()
                        largest_age = max(
                            largest_age,
                            max(
                                (datetime.now(UTC) - value).total_seconds()
                                for value in current.values()
                            ),
                        )
                        assert await dependencies(), current
                        # Both queues must refresh after a full idle cycle.
                        if all(
                            (current[kind] - initial[kind]).total_seconds() >= 50 for kind in kinds
                        ):
                            break
                        await asyncio.sleep(2)
                print(
                    json.dumps(
                        {
                            "idle_seconds": round(time.monotonic() - started, 2),
                            "largest_poller_age_seconds": round(largest_age, 2),
                            "initial": {str(k): v.isoformat() for k, v in initial.items()},
                            "refreshed": {str(k): v.isoformat() for k, v in current.items()},
                        }
                    ),
                    flush=True,
                )
            stopped = time.monotonic()
            async with asyncio.timeout(100):
                while await dependencies():
                    await asyncio.sleep(2)
            print(
                json.dumps(
                    {"rejected_after_worker_stop_seconds": round(time.monotonic() - stopped, 2)}
                ),
                flush=True,
            )

    asyncio.run(exercise())


@pytest.mark.parametrize("cause", ["idle", "expired", "control_plane_lost"])
@pytest.mark.parametrize("requested_first", [False, True])
def test_workflow_cleanup_preserves_first_termination_cause(
    postgres_dsn: str,
    temporal_stack: Any,
    tmp_path: Path,
    cause: str,
    requested_first: bool,
) -> None:
    async def exercise() -> None:
        from datetime import UTC, datetime
        from uuid import UUID

        from boxie.bootstrap.worker import lifecycle_worker
        from boxie.contracts.execution import ObservationWire
        from boxie.domain.lifecycle import OrchestratorObservation
        from boxie.domain.models import SandboxState, TerminationReason
        from boxie.temporal.workflows import SandboxLifecycle
        from boxie_test_support.api import artifact, create
        from temporal_support import durable_stack
        from test_postgres import counts

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        queue = "first-cause-" + uuid4().hex
        async with durable_stack(postgres_dsn, tmp_path, client, queue) as (
            http,
            life,
            orchestrator,
            daemon,
            sessions,
            activities,
            _dispatcher,
        ):
            sid = UUID(await create(http, await artifact(http)))
            request = await life.load_request(sid)
            assert await life.bind_orchestrator_boot(
                OWNER, request.identity, sid, 1, None, orchestrator.boot_id
            )
            if requested_first:
                await life.terminate(OWNER, sid, TerminationReason.REQUESTED)
            observation = OrchestratorObservation(
                sid,
                OWNER,
                request.identity,
                1,
                orchestrator.boot_id,
                1,
                datetime.now(UTC),
                SandboxState.TERMINATING,
                True,
                cleanup_pending=True,
                reason=TerminationReason(cause),
                absolute_deadline=request.absolute_deadline,
            )
            expected = TerminationReason.REQUESTED if requested_first else TerminationReason(cause)
            for item, accepted in ((observation, True), (observation, False)):
                response = await http.put(
                    "/internal/observations",
                    json=ObservationWire.from_domain(item).model_dump(mode="json"),
                    headers={"Authorization": "Bearer orchestrator-test-secret"},
                )
                assert response.status_code == 200
                assert response.json() == {"accepted": accepted}
                assert (await life.get(OWNER, sid)).reason == expected
            assert (await counts(sessions))[1:3] == (1, 1)
            async with lifecycle_worker(client, activities, task_queue=queue):
                handle = await client.start_workflow(
                    SandboxLifecycle.run,
                    str(sid),
                    id=f"boxie/sandbox/{sid}",
                    task_queue=queue,
                )
                assert (await asyncio.wait_for(handle.result(), 30)).cleaned
            final = await life.get(OWNER, sid)
            assert final.reason == expected
            assert final.state == SandboxState.TERMINATED and not final.cleanup_pending
            assert (await life.load_request(sid)).desired_stop
            assert await counts(sessions) == (1, 0, 0, 2)
            assert daemon.creates == 0 and not daemon.resources

    asyncio.run(exercise())


@pytest.mark.parametrize("stop_first", [True, False])
def test_runnable_stack_preserves_accounting_and_worker_replacement(
    postgres_dsn: str,
    temporal_stack: Any,
    tmp_path: Path,
    stop_first: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        from boxie.bootstrap.worker import lifecycle_worker
        from boxie.domain.errors import BoxieError
        from boxie.domain.models import TerminationReason
        from boxie.temporal.workflows import SandboxLifecycle
        from boxie_test_support.api import ready, terminal_url
        from temporal_support import durable_stack
        from temporalio.worker import Replayer
        from test_postgres import counts
        from websockets.asyncio.client import connect

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        queue = "real-lifecycle-" + uuid4().hex
        async with durable_stack(postgres_dsn, tmp_path, client, queue) as (
            http,
            life,
            orchestrator,
            daemon,
            sessions,
            activities,
            dispatcher,
        ):
            import hashlib

            from boxie_test_support.orchestrator import bundle

            artifact_id = str(uuid4())
            content = bundle({"file.txt": b"SECRET-ARTIFACT-BYTES"})
            assert (
                await http.put(
                    f"/v1/artifacts/{artifact_id}",
                    content=content,
                    headers={"X-Content-SHA256": hashlib.sha256(content).hexdigest()},
                )
            ).status_code == 201
            response = await http.post(
                "/v1/sandboxes",
                headers={"Idempotency-Key": "private-history"},
                json={
                    "artifact_id": artifact_id,
                    "profile_id": "shell",
                    "command": ["/bin/sh", "-c", "SECRET-COMMAND"],
                    "env": {"PRIVATE": "SECRET-ENV-SNAPSHOT"},
                },
            )
            assert response.status_code == 202
            identifier = response.json()["id"]
            from uuid import UUID

            sid = UUID(identifier)
            if stop_first:
                await life.terminate(OWNER, sid, TerminationReason.REQUESTED)
            else:
                daemon.lost_ack = True  # Daemon creates, then loses the create response.
                original = activities.orchestrator.ensure_execution
                lost = False

                async def lose_ensure_response(request: Any, boot: Any) -> Any:
                    nonlocal lost
                    result = await original(request, boot)
                    if not lost:
                        lost = True
                        raise BoxieError(503, "lost RPC response SECRET-TRANSPORT")
                    return result

                monkeypatch.setattr(
                    activities.orchestrator, "ensure_execution", lose_ensure_response
                )
            delivery = asyncio.create_task(dispatcher.run())
            try:
                async with lifecycle_worker(client, activities, task_queue=queue):
                    if not stop_first:
                        await ready(http, identifier)
                        assert daemon.creates == 1 and len(daemon.started) == 1
                        ws = await connect(
                            terminal_url(http, identifier),
                            additional_headers={"Authorization": "Bearer consumer-one-secret"},
                        )
                        epoch, _ = await attach(ws, order=1)
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "input",
                                    "epoch": epoch,
                                    "source": "user",
                                    "data": "SECRET-PTY-INPUT",
                                }
                            )
                        )
                        assert (
                            await asyncio.wait_for(ws.recv(), 3)
                            == (1).to_bytes(8, "big") + b"SECRET-PTY-INPUT"
                        )
                    else:
                        await ready(http, identifier, "terminated")
                # Worker replacement must not close an orchestrator-owned live terminal.
                if not stop_first:
                    assert len(daemon.resources) == 1
                    await ws.send(
                        json.dumps(
                            {
                                "type": "input",
                                "epoch": epoch,
                                "source": "user",
                                "data": "SECRET-PTY-INPUT-REPLACEMENT",
                            }
                        )
                    )
                    daemon.unreachable = True
                    await life.terminate(OWNER, sid, TerminationReason.REQUESTED)
                async with lifecycle_worker(client, activities, task_queue=queue):
                    if not stop_first:
                        for _ in range(100):
                            if orchestrator.records[sid].cleanup_pending:
                                break
                            await asyncio.sleep(0.02)
                        assert orchestrator.records[sid].cleanup_pending
                        assert (await counts(sessions))[1:3] == (1, 1)
                        daemon.unreachable = False
                        await ws.close()
                    handle = client.get_workflow_handle(
                        f"boxie/sandbox/{identifier}",
                        result_type=__import__(
                            "boxie.temporal.contracts", fromlist=["LifecycleResult"]
                        ).LifecycleResult,
                    )
                    result = await asyncio.wait_for(handle.result(), 40)
                    assert result.cleaned
                    assert (await counts(sessions))[1:3] == (0, 0)
                    assert not daemon.resources
                    assert daemon.creates == (0 if stop_first else 1)
                    history = await handle.fetch_history()
                    (tmp_path / "production-history.json").write_text(history.to_json())
                    for event in history.events:
                        # Wire bytes include decoded JSON payload data, unlike JSON
                        # history export's base64 representation of those same bytes.
                        encoded = event.SerializeToString()
                        assert b"SECRET-" not in encoded
                        assert (
                            b"consumer-one-secret" not in encoded
                            and b"orchestrator-test-secret" not in encoded
                        )
                    await Replayer(workflows=[SandboxLifecycle]).replay_workflow(history)
                    assert "SECRET-" not in caplog.text
            finally:
                delivery.cancel()
                await asyncio.gather(delivery, return_exceptions=True)

    asyncio.run(exercise())


def test_actual_api_worker_processes_recover_worker_loss(
    postgres_dsn: str,
    temporal_stack: Any,
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        import json
        import socket
        import sys

        import httpx
        from boxie_test_support.api import CONSUMER_AUTH, artifact, create, terminal_url
        from temporal_support import durable_stack
        from websockets.asyncio.client import connect

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        async with durable_stack(postgres_dsn, tmp_path, client, "unused-process-fixture") as (
            _,
            life,
            orchestrator,
            daemon,
            sessions,
            activities,
            _dispatcher,
        ):
            dsn = tmp_path / "postgres.secret"
            dsn.write_text(postgres_dsn)
            dsn.chmod(0o600)
            consumer = tmp_path / "consumer.secret"
            consumer.write_text("consumer-one-secret")
            consumer.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            common = {
                "identity": {
                    "installation_id": str(orchestrator.config.identity.installation_id),
                    "host_id": orchestrator.config.identity.host_id,
                },
                "orchestrator_url": activities.orchestrator._base_url,
                "orchestrator_credential_file": str(orchestrator.config.credential_file),
                "durable": {
                    "postgres_dsn_file": str(dsn),
                    "temporal_address": temporal_stack.address,
                    "temporal_server_name": "temporal",
                    "temporal_ca_file": str(temporal_stack.root / "clients/frontend-ca.pem"),
                    "temporal_cert_file": str(temporal_stack.root / "clients/boxie-api.pem"),
                    "temporal_key_file": str(temporal_stack.root / "clients/boxie-api.key"),
                },
            }
            api_path = tmp_path / "api.json"
            api_path.write_text(
                json.dumps(
                    {
                        **common,
                        "release": "process-test",
                        "consumer_credential_files": {"one": str(consumer)},
                        "bind_port": port,
                    }
                )
            )
            common["durable"]["temporal_cert_file"] = str(
                temporal_stack.root / "clients/boxie-worker.pem"
            )
            common["durable"]["temporal_key_file"] = str(
                temporal_stack.root / "clients/boxie-worker.key"
            )
            worker_path = tmp_path / "worker.json"
            worker_path.write_text(json.dumps(common))
            processes: list[asyncio.subprocess.Process] = []

            async def start(module: str, config: Path) -> asyncio.subprocess.Process:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    module,
                    "--config",
                    str(config),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                processes.append(process)
                return process

            await start("boxie.bootstrap.api", api_path)
            worker = await start("boxie.bootstrap.worker", worker_path)
            try:
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", headers=CONSUMER_AUTH
                ) as http:
                    for _ in range(300):
                        try:
                            response = await http.get("/readyz")
                            if response.status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError("real API/worker did not become ready")
                    assert response.json()["dependencies"] == {"orchestration": True}
                    identifier = await create(http, await artifact(http))
                    for _ in range(300):
                        view = await http.get(f"/v1/sandboxes/{identifier}")
                        if view.json()["state"] == "ready":
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError("real worker did not provision")
                    async with connect(
                        terminal_url(http, identifier), additional_headers=CONSUMER_AUTH
                    ) as ws:
                        epoch, _ = await attach(ws, order=1)
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "input",
                                    "epoch": epoch,
                                    "source": "user",
                                    "data": "PROCESS-PTY-SENTINEL",
                                }
                            )
                        )
                        assert (
                            await asyncio.wait_for(ws.recv(), 3)
                            == (1).to_bytes(8, "big") + b"PROCESS-PTY-SENTINEL"
                        )
                        worker.kill()
                        await asyncio.wait_for(worker.wait(), 5)
                        assert len(daemon.resources) == 1
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "input",
                                    "epoch": epoch,
                                    "source": "user",
                                    "data": "PROCESS-PTY-SENTINEL-after-crash",
                                }
                            )
                        )
                        assert (
                            await asyncio.wait_for(ws.recv(), 3)
                            == (2).to_bytes(8, "big") + b"PROCESS-PTY-SENTINEL-after-crash"
                        )
                        await start("boxie.bootstrap.worker", worker_path)
                        assert (await http.delete(f"/v1/sandboxes/{identifier}")).status_code == 200
                        for _ in range(300):
                            view = await http.get(f"/v1/sandboxes/{identifier}")
                            if view.json()["state"] == "terminated":
                                break
                            await asyncio.sleep(0.1)
                        else:
                            raise AssertionError("replacement worker did not clean")
                    assert daemon.creates == 1 and not daemon.resources
            finally:
                for process in reversed(processes):
                    if process.returncode is None:
                        process.terminate()
                    try:
                        output, _ = await asyncio.wait_for(process.communicate(), 30)
                    except TimeoutError:
                        process.kill()
                        output, _ = await process.communicate()
                        raise AssertionError("process failed to shut down") from None
                    assert b"PROCESS-PTY-SENTINEL" not in output
                    assert (
                        b"consumer-one-secret" not in output
                        and b"orchestrator-test-secret" not in output
                    )

    asyncio.run(exercise())


def test_api_starts_during_temporal_outage_and_recovers(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    async def exercise() -> None:
        import json
        import socket
        import sys
        from contextlib import AsyncExitStack

        import httpx
        from boxie.bootstrap.worker import lifecycle_worker
        from boxie_test_support.api import CONSUMER_AUTH, artifact, create, ready
        from boxie_test_support.orchestrator import request
        from temporal_support import durable_stack

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        available = asyncio.Event()
        connections: set[asyncio.Task[None]] = set()

        async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            # TCP transport gate preserves the actual end-to-end mTLS handshake.
            current = asyncio.current_task()
            assert current is not None
            connections.add(current)
            remote: asyncio.StreamWriter | None = None
            pumps: list[asyncio.Task[None]] = []
            try:
                if not available.is_set():
                    return
                host, port = temporal_stack.address.rsplit(":", 1)
                upstream, remote = await asyncio.open_connection(host, int(port))

                async def copy(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
                    while data := await source.read(65536):
                        target.write(data)
                        await target.drain()

                pumps = [
                    asyncio.create_task(copy(reader, remote)),
                    asyncio.create_task(copy(upstream, writer)),
                ]
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                writer.close()
                if remote:
                    remote.close()
                connections.remove(current)

        async with (
            await asyncio.start_server(proxy, "127.0.0.1", 0) as frontend,
            durable_stack(postgres_dsn, tmp_path, client, "boxie-lifecycle") as stack,
        ):
            fixture_http, _, orchestrator, daemon, _, activities, dispatcher = stack
            artifact_id = await artifact(fixture_http)
            existing = await create(fixture_http, artifact_id, "outage-retry")
            for name, value in (("postgres", postgres_dsn), ("consumer", "consumer-one-secret")):
                path = tmp_path / f"{name}.secret"
                path.write_text(value)
                path.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                api_port = sock.getsockname()[1]
            config = tmp_path / "outage-api.json"
            config.write_text(
                json.dumps(
                    {
                        "release": "outage-test",
                        "bind_port": api_port,
                        "consumer_credential_files": {"one": str(tmp_path / "consumer.secret")},
                        "identity": {
                            "installation_id": str(orchestrator.config.identity.installation_id),
                            "host_id": orchestrator.config.identity.host_id,
                        },
                        "orchestrator_url": activities.orchestrator._base_url,
                        "orchestrator_credential_file": str(orchestrator.config.credential_file),
                        "durable": {
                            "postgres_dsn_file": str(tmp_path / "postgres.secret"),
                            "temporal_address": f"127.0.0.1:{frontend.sockets[0].getsockname()[1]}",
                            "temporal_server_name": "temporal",
                            "temporal_ca_file": str(
                                temporal_stack.root / "clients/frontend-ca.pem"
                            ),
                            "temporal_cert_file": str(
                                temporal_stack.root / "clients/boxie-api.pem"
                            ),
                            "temporal_key_file": str(temporal_stack.root / "clients/boxie-api.key"),
                        },
                    }
                )
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "boxie.bootstrap.api",
                "--config",
                str(config),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            delivery: asyncio.Task[None] | None = None
            try:
                async with (
                    httpx.AsyncClient(
                        base_url=f"http://127.0.0.1:{api_port}", headers=CONSUMER_AUTH, timeout=10
                    ) as http,
                    AsyncExitStack() as running,
                ):
                    for _ in range(100):
                        try:
                            if (await http.get("/healthz")).status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        assert process.returncode is None, "API exited before serving during outage"
                        await asyncio.sleep(0.05)
                    else:
                        raise AssertionError("API did not start during Temporal outage")
                    assert (await http.get(f"/v1/sandboxes/{existing}")).status_code == 200
                    assert await create(http, artifact_id, "outage-retry") == existing
                    assert (await http.get("/readyz")).status_code == 503
                    assert (
                        await http.post(
                            "/v1/sandboxes",
                            json=request(artifact_id)["spec"],
                            headers={"Idempotency-Key": "outage-new"},
                        )
                    ).status_code == 503
                    assert (await http.delete(f"/v1/sandboxes/{existing}")).status_code == 200
                    assert (await http.get(f"/v1/sandboxes/{existing}")).json()[
                        "state"
                    ] == "terminating"
                    available.set()
                    await running.enter_async_context(lifecycle_worker(client, activities))
                    delivery = asyncio.create_task(dispatcher.run())
                    for _ in range(100):
                        if (await http.get("/readyz")).status_code == 200:
                            break
                        await asyncio.sleep(0.05)
                    else:
                        raise AssertionError("API readiness did not recover")
                    admitted = await create(http, artifact_id, "outage-new")
                    assert (await http.delete(f"/v1/sandboxes/{admitted}")).status_code == 200
                    await ready(http, existing, "terminated")
                    await ready(http, admitted, "terminated")
                    assert not daemon.resources
            finally:
                if delivery:
                    delivery.cancel()
                    await asyncio.gather(delivery, return_exceptions=True)
                if process.returncode is None:
                    process.terminate()
                output, _ = await asyncio.wait_for(process.communicate(), 10)
                assert (
                    b"consumer-one-secret" not in output
                    and b"orchestrator-test-secret" not in output
                )
                for connection in list(connections):
                    connection.cancel()
                await asyncio.gather(*connections, return_exceptions=True)

    asyncio.run(exercise())


def test_authenticated_observations_are_atomic_and_fenced(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from dataclasses import replace
        from datetime import UTC, datetime
        from uuid import UUID

        from boxie.contracts.execution import ObservationWire
        from boxie.domain.lifecycle import OrchestratorObservation
        from boxie.domain.models import SandboxState
        from boxie_test_support.api import artifact, create
        from temporal_support import durable_stack

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        async with durable_stack(postgres_dsn, tmp_path, client, "unused-observation-queue") as (
            http,
            life,
            orchestrator,
            _daemon,
            sessions,
            _activities,
            _dispatcher,
        ):
            sid = UUID(await create(http, await artifact(http)))
            await life.bind_orchestrator_boot(
                OWNER, orchestrator.config.identity, sid, 1, None, orchestrator.boot_id
            )
            observation = OrchestratorObservation(
                sid,
                OWNER,
                orchestrator.config.identity,
                1,
                orchestrator.boot_id,
                10,
                datetime.now(UTC),
                SandboxState.READY,
                True,
            )
            body = ObservationWire.from_domain(observation).model_dump(mode="json")
            assert (await http.put("/internal/observations", json=body)).status_code == 401
            orchestrator_headers = {"Authorization": "Bearer orchestrator-test-secret"}
            response = await http.put(
                "/internal/observations", json=body, headers=orchestrator_headers
            )
            assert response.json() == {"accepted": True}
            for stale in (
                replace(observation, event_version=9),
                replace(observation, event_version=11, boot_id=uuid4()),
                replace(observation, event_version=12, generation=2),
            ):
                response = await http.put(
                    "/internal/observations",
                    json=ObservationWire.from_domain(stale).model_dump(mode="json"),
                    headers=orchestrator_headers,
                )
                assert response.json() == {"accepted": False}
            async with sessions() as session:
                observation_record = await session.get(OrchestratorObservationRecord, sid)
                assert observation_record is not None and observation_record.event_version == 10
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Outbox)
                        .where(col(Outbox.sandbox_id) == sid, col(Outbox.kind) == "observation")
                    )
                    == 1
                )
            # Transaction rollback must also roll back the projected fact.
            await add_check(
                sessions, col(Outbox.kind) != "observation", "reject_reports", not_valid=True
            )
            response = await http.put(
                "/internal/observations",
                json=ObservationWire.from_domain(replace(observation, event_version=11)).model_dump(
                    mode="json"
                ),
                headers=orchestrator_headers,
            )
            assert response.status_code == 500
            async with sessions() as session:
                observation_record = await session.get(OrchestratorObservationRecord, sid)
                assert observation_record is not None and observation_record.event_version == 10

    asyncio.run(exercise())


def test_timeout_closed_delivery_and_bounded_claims(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        import time
        from datetime import UTC, datetime

        from boxie.domain.lifecycle import OrchestratorObservation
        from boxie.domain.models import SandboxState
        from boxie.persistence.outbox import PostgresOutbox
        from boxie.temporal.dispatcher import OutboxDispatcher

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            sandbox = await life.submit(OWNER, "timeout", await upload(artifacts))
            store = PostgresOutbox(sessions)
            dispatcher = OutboxDispatcher(store, client, "no-worker-" + uuid4().hex)
            event = (await store.claim("timeout"))[0]
            original = client.start_workflow

            async def lose_response(*args: Any, **kwargs: Any) -> Any:
                await original(*args, **kwargs)
                await asyncio.sleep(10)

            with monkeypatch.context() as patch:
                patch.setattr(client, "start_workflow", lose_response)
                started = time.monotonic()
                with pytest.raises(TimeoutError):
                    await dispatcher.deliver(event)
                assert 4.5 < time.monotonic() - started < 6
            handle = client.get_workflow_handle(f"boxie/sandbox/{sandbox.id}")
            run_id = (await handle.describe()).run_id
            await dispatcher.deliver(event)
            assert (await handle.describe()).run_id == run_id
            await handle.terminate(reason="intentional unexpected closure test")
            with pytest.raises(RuntimeError, match="cleanup obligation"):
                await dispatcher.deliver(event)
            assert not await store.confirmed_terminal(sandbox.id)
            boot = uuid4()
            await life.bind_orchestrator_boot(OWNER, config.identity, sandbox.id, 1, None, boot)
            await life.confirm_removed(
                OrchestratorObservation(
                    sandbox.id,
                    OWNER,
                    config.identity,
                    1,
                    boot,
                    1,
                    datetime.now(UTC),
                    SandboxState.TERMINATED,
                    False,
                    removal_confirmed=True,
                )
            )
            await dispatcher.deliver(event)
            assert await store.acknowledge(event)
            assert (await handle.describe()).run_id == run_id
            # Use another retained request for bounded claims and slow RPC concurrency.
            live = await life.submit(OWNER, "batch", await upload(artifacts))
            async with sessions.begin() as session:
                for index in range(34):
                    await session.exec(
                        insert(Outbox).values(
                            id=uuid4(),
                            deduplication_key=f"batch-{index}",
                            sandbox_id=live.id,
                            kind="observation",
                        )
                    )
            active = maximum = calls = 0

            async def delayed(*args: Any, **kwargs: Any) -> Any:
                nonlocal active, maximum, calls
                active += 1
                maximum = max(maximum, active)
                calls += 1
                try:
                    await asyncio.sleep(0.05)
                    return await original(*args, **kwargs)
                finally:
                    active -= 1

            with monkeypatch.context() as patch:
                patch.setattr(client, "start_workflow", delayed)
                await dispatcher.poll()
            assert 1 < maximum <= 8 and calls <= 32
            claimed = await store.claim("expired")
            assert 1 <= len(claimed) <= 32
            async with sessions.begin() as session:
                await session.exec(
                    update(Outbox)
                    .where(col(Outbox.lease_owner) == "expired")
                    .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
                )
            with pytest.raises(RuntimeError, match="claim expired"):
                await dispatcher.deliver(claimed[0])
            assert not await store.acknowledge(claimed[0])
            await client.get_workflow_handle(f"boxie/sandbox/{live.id}").terminate(
                reason="test fixture cleanup"
            )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "mode", ["cancel_ensure", "cancel_load", "cancel_inspect", "cancel_expire"]
)
def test_production_cancel_during_concurrent_inspection(
    temporal_stack: Any, tmp_path: Path, mode: str
) -> None:
    async def exercise() -> None:
        from boxie.temporal.activities import SafeActivityErrors
        from boxie.temporal.workflows import SandboxLifecycle
        from temporalio.worker import Replayer, Worker
        from test_temporal_lifecycle import Decisions

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        decisions = Decisions(mode)
        queue = "production-cancel-" + uuid4().hex
        async with Worker(
            client,
            task_queue=queue,
            workflows=[SandboxLifecycle],
            activities=decisions.activities(),
            interceptors=[SafeActivityErrors()],
        ):
            handle = await client.start_workflow(
                SandboxLifecycle.run, str(uuid4()), id=str(uuid4()), task_queue=queue
            )
            try:
                await asyncio.wait_for(decisions.entered.wait(), 5)
                await handle.cancel()
                assert (await asyncio.wait_for(handle.result(), 20)).cleaned
                history = await handle.fetch_history()
                (tmp_path / "production-cancellation.json").write_text(history.to_json())
                await Replayer(workflows=[SandboxLifecycle]).replay_workflow(history)
            finally:
                decisions.release.set()
                history = await handle.fetch_history()
                (tmp_path / "production-cancellation-final.json").write_text(history.to_json())
                try:
                    await handle.terminate(reason="fixture cleanup if still pending")
                except Exception:
                    pass

    asyncio.run(exercise())


def test_backlog_threshold_preserves_matching_retry_status_and_stop(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from boxie.domain.errors import BoxieError
        from boxie.domain.models import TerminationReason

        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _config):
            spec = await upload(artifacts)
            first = await life.submit(OWNER, "original", spec)
            async with sessions.begin() as session:
                for index in range(98):
                    await session.exec(
                        insert(Outbox).values(
                            id=uuid4(),
                            deduplication_key=f"readiness-{index}",
                            sandbox_id=first.id,
                            kind="start",
                        )
                    )
            assert await life.ready()  # backlog 99
            await life.submit(OWNER, "at-99", spec)
            assert not await life.ready()  # backlog 100
            with pytest.raises(BoxieError) as unavailable:
                await life.submit(OWNER, "at-100", spec)
            assert unavailable.value.status == 503
            assert (await life.submit(OWNER, "original", spec)).id == first.id
            assert (await life.get(OWNER, first.id)).id == first.id
            assert (
                await life.terminate(OWNER, first.id, TerminationReason.REQUESTED)
            ).cleanup_pending
            async with sessions.begin() as session:
                await session.exec(update(Outbox).values(delivered_at=func.clock_timestamp()))
                await session.exec(
                    update(Outbox)
                    .where(col(Outbox.deduplication_key) == f"{first.id}:start")
                    .values(
                        delivered_at=None, created_at=func.clock_timestamp() - timedelta(seconds=29)
                    )
                )
            assert await life.ready()
            async with sessions.begin() as session:
                await session.exec(
                    update(Outbox)
                    .where(col(Outbox.delivered_at).is_(None))
                    .values(created_at=func.clock_timestamp() - timedelta(seconds=31))
                )
            assert not await life.ready()

    asyncio.run(exercise())


def test_workflow_cancel_retains_uncertain_create_until_actual_removal(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    async def exercise() -> None:
        from uuid import UUID

        from boxie.bootstrap.worker import lifecycle_worker
        from boxie.temporal.contracts import LifecycleResult
        from boxie.temporal.workflows import SandboxLifecycle
        from boxie_test_support.api import artifact, create
        from temporal_support import durable_stack
        from temporalio.worker import Replayer
        from test_postgres import counts

        client = await Client.connect(
            temporal_stack.address, namespace="boxie", tls=tls(temporal_stack, "boxie-worker")
        )
        queue = "cancel-accounting-" + uuid4().hex
        async with durable_stack(postgres_dsn, tmp_path, client, queue) as (
            http,
            life,
            orchestrator,
            daemon,
            sessions,
            activities,
            dispatcher,
        ):
            gate = asyncio.Event()
            daemon.create_gate = gate
            identifier = await create(http, await artifact(http))
            sid = UUID(identifier)
            delivery = asyncio.create_task(dispatcher.run())
            try:
                async with lifecycle_worker(client, activities, task_queue=queue):
                    await asyncio.wait_for(daemon.create_entered.wait(), 5)
                    handle = client.get_workflow_handle(
                        f"boxie/sandbox/{identifier}", result_type=LifecycleResult
                    )
                    await handle.cancel()
                    for _ in range(1500):
                        if (await life.load_request(sid)).desired_stop:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError("cancellation stop not persisted")
                    assert (await counts(sessions))[1:3] == (1, 1)
                    assert not orchestrator.records[sid].removal_confirmed
                    gate.set()  # Earlier 404s cannot settle this still-running create.
                    assert (await asyncio.wait_for(handle.result(), 30)).cleaned
                    assert daemon.creates == 1 and not daemon.started and not daemon.resources
                    assert (await counts(sessions))[1:3] == (0, 0)
                    history = await handle.fetch_history()
                    (tmp_path / "cancel-accounting.json").write_text(history.to_json())
                    await Replayer(workflows=[SandboxLifecycle]).replay_workflow(history)
            finally:
                gate.set()
                delivery.cancel()
                await asyncio.gather(delivery, return_exceptions=True)

    asyncio.run(exercise())
