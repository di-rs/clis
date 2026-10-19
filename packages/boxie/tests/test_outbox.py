"""SQL outbox handoff and embedded execution against production Temporal and SQL."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from boxie.bootstrap.schedules import check_schedules, ensure_schedules, schedule_definitions
from boxie.bootstrap.workers import embedded_workers
from boxie.domain.lifecycle import OrchestratorObservation
from boxie.domain.models import SandboxState, TerminationReason
from boxie.persistence.models import Outbox
from boxie.temporal.contracts import LifecycleResult
from boxie.temporal.workflows import SandboxLifecycle
from boxie_test_support.api import artifact, create, ready
from database_support import OWNER, counts, database, upload
from sqlalchemy import func, insert, update
from sqlmodel import col
from temporal_support import lifecycle_stack, tls
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

pytestmark = pytest.mark.integration


def test_idle_recovery_survives_database_failure_without_polling_workflows(
    postgres_dsn: str,
    temporal_stack: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from boxie.persistence.outbox import OutboxEvent, PostgresOutbox
    from boxie.temporal.contracts import maintenance_queue
    from boxie.temporal.maintenance import MaintenanceActivities

    caplog.set_level(logging.INFO, logger="boxie")

    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            pending = PostgresOutbox.pending
            recovered = asyncio.Event()
            attempts = 0

            async def unavailable_once(
                store: PostgresOutbox, sandbox_id: UUID | None = None
            ) -> list[OutboxEvent]:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("temporary database outage")
                events = await pending(store, sandbox_id)
                if attempts >= 3:
                    recovered.set()
                return events

            monkeypatch.setattr(PostgresOutbox, "pending", unavailable_once)
            async with embedded_workers(client, stack.life, stack.orchestrator, stack.artifacts):
                await asyncio.wait_for(recovered.wait(), 15)
                assert not [
                    execution
                    async for execution in client.list_workflows(
                        query=f'TaskQueue = "{maintenance_queue(stack.orchestrator.config.identity)}"'
                    )
                    if "/commands-" in execution.id or "/journal-" in execution.id
                ]
                maintenance = MaintenanceActivities(
                    stack.delivery, stack.life, stack.orchestrator, stack.artifacts
                )
                await maintenance.maintain("artifacts")
                await maintenance.maintain("retention")
                assert [
                    record.getMessage()
                    for record in caplog.records
                    if record.name.startswith("boxie.temporal.")
                ] == ["command recovery unavailable"]
                for identifier in schedule_definitions(stack.orchestrator.config.identity):
                    await client.get_schedule_handle(identifier).delete()

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["artifacts", "artifacts_unavailable", "retention"])
def test_daily_cleanup_drains_more_than_one_batch(
    postgres_dsn: str,
    temporal_stack: Any,
    tmp_path: Path,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import shutil
    import time

    from boxie.persistence.models import Artifact, Sandbox
    from boxie.temporal.maintenance import MaintenanceActivities
    from sqlmodel import select

    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            live = await upload(stack.artifacts)
            live_path = await stack.artifacts.path(OWNER, live.artifact_id)
            old = datetime.now(UTC) - timedelta(days=8)
            blocked: Path | None = None
            async with stack.sessions.begin() as session:
                if kind.startswith("artifacts"):
                    for index in range(101):
                        storage = uuid4()
                        (stack.artifacts.root / str(storage)).mkdir()
                        if kind == "artifacts_unavailable" and index == 0:
                            blocked = stack.artifacts.root / str(storage)
                        orphan = stack.artifacts.root / str(uuid4())
                        orphan.mkdir()
                        os.utime(orphan, (time.time() - 7200, time.time() - 7200))
                        session.add(
                            Artifact(
                                id=uuid4(),
                                consumer_id="one",
                                digest="0" * 64,
                                storage_id=storage,
                                retired=True,
                                collection_pending=True,
                                created_at=old + timedelta(seconds=index),
                            )
                        )
                else:
                    for index in range(1001):
                        session.add(
                            Sandbox(
                                id=uuid4(),
                                consumer_id="one",
                                idempotency_key=f"expired-{index}",
                                request_hash="0" * 64,
                                spec={},
                                snapshot={},
                                installation_id=stack.orchestrator.config.identity.installation_id,
                                host_id=stack.orchestrator.config.identity.host_id,
                                accepted_at=old,
                                absolute_deadline=old + timedelta(hours=1),
                                state="terminated",
                                desired_stop=True,
                                cleaned_at=old,
                            )
                        )
            maintenance = MaintenanceActivities(
                stack.delivery, stack.life, stack.orchestrator, stack.artifacts
            )
            remove = shutil.rmtree

            def fail_one(path: Path) -> None:
                if path == blocked:
                    raise OSError("one artifact is unavailable")
                remove(path)

            with monkeypatch.context() as patch:
                patch.setattr(shutil, "rmtree", fail_one)
                await maintenance.maintain("artifacts" if kind.startswith("artifacts") else kind)
            assert live_path.is_dir()
            if kind.startswith("artifacts"):
                assert set(stack.artifacts.root.iterdir()) == {live_path.parent} | (
                    {blocked} if blocked else set()
                )
            else:
                async with stack.sessions() as session:
                    assert (await session.exec(select(Sandbox))).all() == []

    asyncio.run(exercise())


@pytest.mark.parametrize("previous", [False, True])
def test_upgrade_replaces_polling_schedules_with_twice_daily_cleanup(
    temporal_stack: Any, previous: bool
) -> None:
    from boxie.domain.models import OrchestratorIdentity
    from boxie.temporal.contracts import maintenance_queue
    from boxie.temporal.maintenance import Maintenance
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleSpec,
    )
    from temporalio.service import RPCError, RPCStatusCode

    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        identity = OrchestratorIdentity(uuid4(), "upgrade")
        queue = maintenance_queue(identity)
        identifiers = [
            f"{queue}/{kind}" for kind in ("commands", "journal", "artifacts", "retention")
        ]
        try:
            for kind, seconds in (
                ("commands", 15 if previous else 5),
                ("journal", 60 if previous else 30),
                ("artifacts", 300 if previous else 30),
                ("retention", 300 if previous else 30),
            ):
                await client.create_schedule(
                    f"{queue}/{kind}",
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            Maintenance.run, kind, id=f"{queue}/{kind}", task_queue=queue
                        ),
                        spec=ScheduleSpec(
                            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=seconds))]
                        ),
                        policy=SchedulePolicy(
                            overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(minutes=1)
                        ),
                    ),
                )
            await ensure_schedules(client, identity)
            await ensure_schedules(client, identity)
            for kind in ("commands", "journal"):
                with pytest.raises(RPCError) as missing:
                    await client.get_schedule_handle(f"{queue}/{kind}").describe()
                assert missing.value.status == RPCStatusCode.NOT_FOUND
            for kind in ("artifacts", "retention"):
                description = await client.get_schedule_handle(f"{queue}/{kind}").describe()
                assert description.schedule.spec.intervals == [
                    ScheduleIntervalSpec(every=timedelta(hours=12))
                ]
            await check_schedules(client, identity)
        finally:
            for identifier in identifiers:
                try:
                    await client.get_schedule_handle(identifier).delete()
                except RPCError as error:
                    if error.status != RPCStatusCode.NOT_FOUND:
                        raise

    asyncio.run(exercise())


@pytest.mark.parametrize("stop_first", [False, True])
def test_committed_command_recovers_without_api_delivery(
    postgres_dsn: str,
    temporal_stack: Any,
    tmp_path: Path,
    stop_first: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            spec = await upload(stack.artifacts)
            view = await stack.life.submit(OWNER, "lost-api", spec)
            if stop_first:
                await stack.life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            stack.daemon.lost_ack = True
            from boxie.persistence.outbox import PostgresOutbox

            acknowledge = PostgresOutbox.acknowledge
            lost_sql_ack = False

            async def fail_ack_once(store: PostgresOutbox, event: Any) -> bool:
                nonlocal lost_sql_ack
                if event.sandbox_id == view.id and not lost_sql_ack:
                    lost_sql_ack = True
                    raise OSError("delivery committed to Temporal, SQL acknowledgement unavailable")
                return await acknowledge(store, event)

            monkeypatch.setattr(PostgresOutbox, "acknowledge", fail_ack_once)
            async with embedded_workers(client, stack.life, stack.orchestrator, stack.artifacts):
                async with asyncio.timeout(15):
                    while (await stack.life.get(OWNER, view.id)).state != (
                        "terminated" if stop_first else "ready"
                    ):
                        await asyncio.sleep(0.1)
                assert lost_sql_ack
                async with asyncio.timeout(10):
                    while await stack.delivery.store.pending(view.id):
                        await asyncio.sleep(0.05)
                first_run = (
                    await client.get_workflow_handle(f"boxie/sandbox/{view.id}").describe()
                ).run_id
                await stack.delivery.recover_once()
                assert (
                    await client.get_workflow_handle(f"boxie/sandbox/{view.id}").describe()
                ).run_id == first_run
                if not stop_first:
                    assert len(stack.daemon.started) == 1
                    # Redelivery after a lost SQL delivery acknowledgement is harmless.
                    await stack.delivery.recover_once()
                    assert (await stack.http.delete(f"/v1/sandboxes/{view.id}")).status_code == 200
                handle = client.get_workflow_handle(
                    f"boxie/sandbox/{view.id}", result_type=LifecycleResult
                )
                assert (await asyncio.wait_for(handle.result(), 40)).cleaned
                assert not stack.daemon.resources
                assert stack.daemon.creates == (0 if stop_first else 1)
                assert (await counts(stack.sessions))[1:3] == (0, 0)
                history = await handle.fetch_history()
                assert all(b"SECRET-" not in event.SerializeToString() for event in history.events)
                await Replayer(
                    workflows=[SandboxLifecycle], data_converter=pydantic_data_converter
                ).replay_workflow(history)
                await check_schedules(client, stack.orchestrator.config.identity)
                for identifier in schedule_definitions(stack.orchestrator.config.identity):
                    await client.get_schedule_handle(identifier).delete()

    asyncio.run(exercise())


def test_cancelled_activity_retains_late_create_and_accounting(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            stack.daemon.create_gate = asyncio.Event()
            async with embedded_workers(client, stack.life, stack.orchestrator, stack.artifacts):
                identifier = await create(stack.http, await artifact(stack.http))
                await asyncio.wait_for(stack.daemon.create_entered.wait(), 5)
                handle = client.get_workflow_handle(
                    f"boxie/sandbox/{identifier}", result_type=LifecycleResult
                )
                await handle.cancel()
                for _ in range(500):
                    if (await stack.life.load_request(UUID(identifier))).desired_stop:
                        break
                    await asyncio.sleep(0.02)
                assert (await stack.life.load_request(UUID(identifier))).desired_stop
                assert (await counts(stack.sessions))[1:3] == (1, 1)
                assert not stack.orchestrator.records[UUID(identifier)].removal_confirmed
                stack.daemon.create_gate.set()
                assert (await asyncio.wait_for(handle.result(), 40)).cleaned
                assert not stack.daemon.started and not stack.daemon.resources
                assert (await counts(stack.sessions))[1:3] == (0, 0)
                await Replayer(
                    workflows=[SandboxLifecycle], data_converter=pydantic_data_converter
                ).replay_workflow(await handle.fetch_history())
                for schedule in schedule_definitions(stack.orchestrator.config.identity):
                    await client.get_schedule_handle(schedule).delete()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "cause,requested_first", [("idle", False), ("disconnected", False), ("idle", True)]
)
def test_cleanup_preserves_first_termination_cause(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path, cause: str, requested_first: bool
) -> None:
    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            view = await stack.life.submit(OWNER, "first-cause", await upload(stack.artifacts))
            identity, boot = stack.orchestrator.config.identity, stack.orchestrator.boot_id
            assert await stack.life.bind_orchestrator_boot(OWNER, identity, view.id, 1, None, boot)
            if requested_first:
                await stack.life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            observation = OrchestratorObservation(
                view.id,
                OWNER,
                identity,
                1,
                boot,
                1,
                datetime.now(UTC),
                SandboxState.TERMINATING,
                True,
                cleanup_pending=True,
                reason=TerminationReason(cause),
            )
            assert await stack.life.record_observation(observation)
            assert not await stack.life.record_observation(replace(observation, boot_id=uuid4()))
            async with embedded_workers(client, stack.life, stack.orchestrator, stack.artifacts):
                await stack.delivery.recover_once()
                handle = client.get_workflow_handle(
                    f"boxie/sandbox/{view.id}", result_type=LifecycleResult
                )
                assert (await asyncio.wait_for(handle.result(), 40)).cleaned
                assert (await stack.life.get(OWNER, view.id)).reason == (
                    "requested" if requested_first else cause
                )
                assert not stack.daemon.resources and stack.daemon.creates == 0
                for schedule in schedule_definitions(identity):
                    await client.get_schedule_handle(schedule).delete()

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
        from workflow_support import Decisions

        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
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
                await Replayer(
                    workflows=[SandboxLifecycle], data_converter=pydantic_data_converter
                ).replay_workflow(history)
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


def test_actual_api_process_accepts_status_and_stop_during_temporal_outage(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    import json
    import socket
    import sys

    import httpx

    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            view = await stack.life.submit(OWNER, "api-outage", await upload(stack.artifacts))
            dsn_path = tmp_path / "dsn.secret"
            dsn_path.write_text(postgres_dsn)
            dsn_path.chmod(0o600)
            consumer = tmp_path / "consumer.secret"
            consumer.write_text("consumer-one-secret")
            consumer.chmod(0o600)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            configuration = {
                "release": "outage",
                "bind_port": port,
                "identity": {
                    "installation_id": str(stack.orchestrator.config.identity.installation_id),
                    "host_id": stack.orchestrator.config.identity.host_id,
                },
                "orchestrator_url": str(stack.life.resolver._http.base_url),
                "orchestrator_credential_file": str(stack.orchestrator.config.credential_file),
                "consumer_credential_files": {"one": str(consumer)},
                "lifecycle": {
                    "postgres_dsn_file": str(dsn_path),
                    "temporal_address": "127.0.0.1:1",
                    "temporal_server_name": "temporal",
                    "temporal_ca_file": str(temporal_stack.root / "clients/frontend-ca.pem"),
                    "temporal_cert_file": str(temporal_stack.root / "clients/boxie-api.pem"),
                    "temporal_key_file": str(temporal_stack.root / "clients/boxie-api.key"),
                },
            }
            path = tmp_path / "api.json"
            path.write_text(json.dumps(configuration))
            with (tmp_path / "api.log").open("wb") as output:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "boxie.bootstrap.api",
                    "--config",
                    str(path),
                    stdout=output,
                    stderr=output,
                )
                try:
                    async with httpx.AsyncClient(
                        base_url=f"http://127.0.0.1:{port}",
                        headers={"Authorization": "Bearer consumer-one-secret"},
                        timeout=10,
                    ) as http:
                        async with asyncio.timeout(10):
                            while True:
                                try:
                                    if (await http.get("/healthz")).status_code == 200:
                                        break
                                except httpx.HTTPError:
                                    pass
                                await asyncio.sleep(0.05)
                        assert (await http.get(f"/v1/sandboxes/{view.id}")).status_code == 200
                        assert (await http.get("/readyz")).status_code == 503
                        assert (await http.delete(f"/v1/sandboxes/{view.id}")).status_code == 200
                        assert (await stack.life.load_request(view.id)).desired_stop
                        assert (await counts(stack.sessions))[1:3] == (1, 1)
                finally:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 5)

    asyncio.run(exercise())


def test_busy_maintenance_preserves_terminal_and_lease_and_cleanup_history(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from boxie.temporal.contracts import maintenance_queue
    from boxie.temporal.maintenance import Maintenance
    from boxie_test_support.api import CONSUMER_AUTH, terminal_url
    from boxie_test_support.terminal_network import attach
    from temporalio.api.enums.v1 import EventType
    from websockets.asyncio.client import connect

    async def exercise() -> None:
        client = await Client.connect(
            temporal_stack.address,
            namespace="boxie",
            tls=tls(temporal_stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            gate, saturated = asyncio.Event(), asyncio.Event()
            active = 0
            original_collect = stack.artifacts.collect

            async def held_collect(limit: int = 100) -> int:
                nonlocal active
                active += 1
                if active >= 4:
                    saturated.set()
                try:
                    await gate.wait()
                    return int(await original_collect(limit))
                finally:
                    active -= 1

            monkeypatch.setattr(stack.artifacts, "collect", held_collect)
            async with embedded_workers(client, stack.life, stack.orchestrator, stack.artifacts):
                identifier = await create(stack.http, await artifact(stack.http))
                await ready(stack.http, identifier)
                sid = UUID(identifier)
                first_lease = stack.orchestrator.records[sid].control_lease_until
                tasks = [
                    await client.start_workflow(
                        Maintenance.run,
                        "artifacts",
                        id="busy-" + uuid4().hex,
                        task_queue=maintenance_queue(stack.orchestrator.config.identity),
                    )
                    for _ in range(4)
                ]
                try:
                    await asyncio.wait_for(saturated.wait(), 5)
                    async with connect(
                        terminal_url(stack.http, identifier), additional_headers=CONSUMER_AUTH
                    ) as terminal:
                        epoch, _ = await attach(terminal, order=1)
                        await terminal.send(
                            json.dumps(
                                {
                                    "type": "input",
                                    "epoch": epoch,
                                    "source": "user",
                                    "data": "responsive",
                                }
                            )
                        )
                        assert (await asyncio.wait_for(terminal.recv(), 2))[8:] == b"responsive"
                        async with asyncio.timeout(40):
                            while (
                                stack.orchestrator.records[sid].control_lease_until <= first_lease
                            ):
                                await asyncio.sleep(0.1)
                        stack.daemon.unreachable = True
                        assert (
                            await stack.http.delete(f"/v1/sandboxes/{identifier}")
                        ).status_code == 200
                        await asyncio.sleep(5)
                        handle = client.get_workflow_handle(
                            f"boxie/sandbox/{identifier}", result_type=LifecycleResult
                        )
                        history = await handle.fetch_history()
                        signals = [
                            event
                            for event in history.events
                            if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
                        ]
                        assert len(signals) <= 2, "cleanup retries keep appending workflow signals"
                        assert (await counts(stack.sessions))[1:3] == (1, 1)
                        stack.daemon.unreachable = False
                        assert (await asyncio.wait_for(handle.result(), 30)).cleaned
                finally:
                    stack.daemon.unreachable = False
                    gate.set()
                    await asyncio.gather(*(task.result() for task in tasks))
                    for schedule in schedule_definitions(stack.orchestrator.config.identity):
                        await client.get_schedule_handle(schedule).delete()

    asyncio.run(exercise())
