"""Committed lifecycle facts, deduplication, and rebuilt SQL gauges."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from boxie.domain.lifecycle import SupervisorObservation
from boxie.domain.models import SandboxState, TerminationReason
from boxie.observability.events import Transition
from database_support import OWNER, add_check, database, upload
from test_observability_metrics import metrics_type


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["ready", "failed", "stopped", "late_ready", "coalesced_ready"])
def test_committed_outcomes_and_rebuilt_gauges(
    postgres_dsn: str, tmp_path: Path, outcome: str
) -> None:
    Metrics, _ = metrics_type()
    from boxie.observability.snapshot import operational_snapshot

    async def exercise() -> None:
        metrics = Metrics("api")
        events: list[Transition] = []

        def sink(event: Transition) -> None:
            events.append(event)
            metrics.transition(event)

        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, config):
            life.diagnostics = sink
            spec = await upload(artifacts)
            from boxie.persistence.postgres import PostgresLifecycle

            other = PostgresLifecycle(
                pool, life.resolver, config.identity, life.dependencies, diagnostics=sink
            )
            views = await asyncio.gather(
                *(
                    repository.submit(OWNER, "one", spec)
                    for repository in (life, other, life, other)
                )
            )
            assert len({item.id for item in views}) == 1
            view = views[0]
            metrics.refresh(await operational_snapshot(pool))
            assert b'boxie_sandboxes{state="accepted"} 1.0' in metrics.render()
            assert [e.event for e in events] == ["sandbox.accepted"]
            boot = uuid4()
            await life.bind_supervisor_boot(OWNER, config.identity, view.id, 1, None, boot)
            ready = outcome in ("ready", "coalesced_ready")
            item = SupervisorObservation(
                view.id,
                OWNER,
                config.identity,
                1,
                boot,
                1,
                datetime.now(UTC),
                SandboxState.READY if ready else SandboxState.FAILED,
                ready,
                reason=TerminationReason.PROVISIONING_FAILED if outcome == "failed" else None,
                ready_at=datetime.now(UTC) if ready else None,
            )
            if outcome == "coalesced_ready":
                item = replace(
                    item,
                    state=SandboxState.TERMINATED,
                    resource_present=False,
                    removal_confirmed=True,
                )
            assert await life.record_observation(item)
            assert not await life.record_observation(item)
            metrics.refresh(await operational_snapshot(pool))
            projected = (
                "ready"
                if ready and outcome != "coalesced_ready"
                else "terminated"
                if outcome == "coalesced_ready"
                else "failed"
            )
            assert f'boxie_sandboxes{{state="{projected}"}} 1.0'.encode() in metrics.render()
            if outcome in ("failed", "stopped", "late_ready"):
                assert not any(event.event == "provisioning.completed" for event in events)
            if outcome == "ready":
                # Fresh renewal lacks readiness, then a boot rebind discards the
                # latest observation. Immutable resolution survives both.
                item = replace(item, event_version=2, ready_at=None)
                assert await other.record_observation(item)
                newer_boot = uuid4()
                assert await life.bind_supervisor_boot(
                    OWNER, config.identity, view.id, 1, boot, newer_boot
                )
                item = replace(item, boot_id=newer_boot)
            if outcome != "coalesced_ready":
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
                metrics.refresh(await operational_snapshot(pool))
                assert b"boxie_cleanup_pending 1.0" in metrics.render()
                assert b"boxie_capacity_reserved 1.0" in metrics.render()
                if outcome == "late_ready":
                    item = replace(item, event_version=2, ready_at=datetime.now(UTC))
                    assert await life.record_observation(item)
                final = replace(
                    item,
                    event_version=3,
                    state=SandboxState.FAILED if outcome == "failed" else SandboxState.TERMINATED,
                    removal_confirmed=True,
                    resource_present=False,
                    cleanup_pending=False,
                )
                assert await life.confirm_removed(final)
                assert not await life.confirm_removed(final)
                assert not await life.record_observation(
                    replace(final, event_version=4, ready_at=datetime.now(UTC))
                )
            snapshot = await operational_snapshot(pool)
            restarted = Metrics("api")
            restarted.refresh(snapshot)
            assert b"boxie_capacity_reserved 0.0" in restarted.render()
            assert b"boxie_cleanup_pending 0.0" in restarted.render()
            completed = [e for e in events if e.event == "provisioning.completed"]
            expected = "ready" if outcome in ("ready", "late_ready", "coalesced_ready") else outcome
            assert len(completed) == 1 and completed[0].outcome == expected
            assert sum(e.event == "sandbox.ready" for e in events) == int(expected == "ready")
            assert sum(e.event == "cleanup.completed" for e in events) == 1
            assert sum(e.event == "termination.requested" for e in events) == int(
                outcome != "coalesced_ready"
            )
            assert (completed[0].duration_seconds is not None) == (expected == "ready")
            if expected == "ready":
                assert completed[0].duration_seconds is not None
                assert completed[0].duration_seconds > 0
            assert (
                f"boxie_provisioning_ready_observation_duration_seconds_count {float(expected == 'ready')}".encode()
                in metrics.render()
            )
            assert (
                b'boxie_provisioning_total{outcome="' + expected.encode() + b'"} 1.0'
                in metrics.render()
            )

    asyncio.run(exercise())


def test_supervisor_attachment_and_cleanup_attempt_events(tmp_path: Path) -> None:
    from lifecycle_support import settled, setup

    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)
        events: list[Transition] = []
        supervisor.diagnostics = events.append
        try:
            await settled(supervisor, request.ref, SandboxState.READY)
            await supervisor.reconcile()
            assert events == []
            epoch = await supervisor.attach(request.ref, attachment_order=1)
            await supervisor.write(request.ref, epoch, b"never-log-this")
            await supervisor.detach(request.ref, epoch)
            await supervisor.detach(request.ref, epoch)
            assert [e.event for e in events] == ["terminal.attached", "terminal.detached"]
            daemon.unreachable = True
            await supervisor.remove(request.ref, TerminationReason.REQUESTED)
            assert events[-1].event == "cleanup.failed"
            daemon.unreachable = False
            await supervisor.reconcile()
            assert events[-1].event == "reconciliation.completed"
            assert events[-1].outcome == "confirmed"
            assert not any(
                e.event in ("sandbox.ready", "provisioning.completed", "cleanup.completed")
                for e in events
            )
        finally:
            await supervisor.close()

    asyncio.run(exercise())


@pytest.mark.integration
def test_rollback_and_diagnostic_failure_preserve_commit_semantics(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from boxie.observability.snapshot import operational_snapshot
    from boxie.persistence.models import Outbox
    from sqlalchemy import CheckConstraint
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.schema import DropConstraint
    from sqlmodel import col

    async def exercise() -> None:
        events: list[Transition] = []
        async with database(postgres_dsn, tmp_path) as (life, artifacts, pool, _):
            life.diagnostics = events.append
            spec = await upload(artifacts)
            await add_check(pool, col(Outbox.kind) != "start", "fail_start")
            with pytest.raises(IntegrityError):
                await life.submit(OWNER, "rollback", spec)
            assert events == []
            assert (await operational_snapshot(pool)).reserved == 0
            async with pool.begin() as session:
                connection = await session.connection()
                constraint = CheckConstraint(col(Outbox.kind) != "start", name="fail_start")
                try:
                    await connection.execute(DropConstraint(constraint))
                finally:
                    constraint.table.constraints.discard(constraint)

            def broken_sink(event: Transition) -> None:
                raise RuntimeError("unavailable telemetry")

            life.diagnostics = broken_sink
            accepted = await life.submit(OWNER, "valid", spec)
            assert (await life.get(OWNER, accepted.id)).state == SandboxState.ACCEPTED
            assert (await operational_snapshot(pool)).reserved == 1
            stopped = await life.terminate(OWNER, accepted.id, TerminationReason.REQUESTED)
            assert stopped.cleanup_pending

    asyncio.run(exercise())


@pytest.mark.integration
def test_real_sql_http_worker_history_replay_does_not_emit(
    postgres_dsn: str,
    temporal_stack: object,
    tmp_path: Path,
) -> None:
    from typing import Any, cast
    from uuid import UUID

    from boxie.bootstrap.workers import embedded_workers
    from boxie.observability.activity_context import build_activity_context_resolver
    from boxie.observability.metrics import Metrics
    from boxie.observability.snapshot import operational_snapshot
    from boxie.temporal.workflows import SandboxLifecycle
    from boxie_test_support.api import artifact, ready
    from temporal_support import lifecycle_stack, tls
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.worker import Replayer

    async def exercise() -> None:
        fixture = cast(Any, temporal_stack)
        client = await Client.connect(
            fixture.address,
            namespace="boxie",
            tls=tls(fixture, "boxie-supervisor"),
            data_converter=pydantic_data_converter,
        )
        async with lifecycle_stack(postgres_dsn, tmp_path, client) as stack:
            metrics = Metrics("api")
            events: list[Transition] = []

            def sink(event: Transition) -> None:
                events.append(event)
                metrics.transition(event)

            stack.life.diagnostics = sink
            async with embedded_workers(
                client,
                stack.life,
                stack.supervisor,
                stack.artifacts,
                context_resolver=build_activity_context_resolver(stack.sessions),
            ):
                creation, stop = uuid4(), uuid4()
                response = await stack.http.post(
                    "/v1/sandboxes",
                    json={"artifact_id": await artifact(stack.http)},
                    headers={"Idempotency-Key": "correlation", "X-Request-ID": str(creation)},
                )
                assert response.status_code == 202
                identifier = response.json()["id"]
                await ready(stack.http, identifier)
                stack.daemon.unreachable = True
                assert (
                    await stack.http.delete(
                        "/v1/sandboxes/" + identifier, headers={"X-Request-ID": str(stop)}
                    )
                ).status_code == 200
                metrics.refresh(await operational_snapshot(stack.sessions))
                assert b"boxie_capacity_reserved 1.0" in metrics.render()
                assert b"boxie_cleanup_pending 1.0" in metrics.render()
                stack.daemon.unreachable = False
                handle = client.get_workflow_handle("boxie/sandbox/" + identifier)
                await asyncio.wait_for(handle.result(), 40)
                assert not (await stack.life.get(OWNER, UUID(identifier))).cleanup_pending
                history = await handle.fetch_history()
                before = [(e.event, e.outcome) for e in events]
                await Replayer(
                    workflows=[SandboxLifecycle], data_converter=pydantic_data_converter
                ).replay_workflow(history)
                assert [(e.event, e.outcome) for e in events] == before
                assert metrics.provisioning.labels("ready")._value.get() == 1
                assert sum(e.event == "cleanup.completed" for e in events) == 1
                assert all(
                    e.request_id == creation
                    for e in events
                    if e.event in {"sandbox.accepted", "provisioning.completed", "sandbox.ready"}
                )
                assert all(
                    e.request_id == stop
                    for e in events
                    if e.event in {"termination.requested", "cleanup.completed"}
                )
                restarted = Metrics("api")
                restarted.refresh(await operational_snapshot(stack.sessions))
                assert b"boxie_capacity_reserved 0.0" in restarted.render()

    asyncio.run(exercise())


def test_domain_events_reach_safe_logging_catalog() -> None:
    import json
    import os

    from boxie.observability.events import EVENTS
    from boxie.observability.telemetry.logging import LogDispatcher, LogSettings

    read, write = os.pipe()
    dispatcher = LogDispatcher(
        LogSettings(service="boxie", role="api"), EVENTS, {}, write, 4096, "enabled"
    )
    try:
        for event in EVENTS:
            dispatcher.emit(
                event,
                sandbox_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                reason="provisioning_failed",
            )
        dispatcher.close()
        records = [json.loads(line) for line in os.read(read, 65536).splitlines()]
        assert {item["event"] for item in records} == set(EVENTS)
        assert all(item["reason"] == "provisioning_failed" for item in records)
    finally:
        os.close(read)


@pytest.mark.integration
def test_telemetry_pool_timeout_does_not_consume_lifecycle_capacity(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from boxie.observability.metrics import Metrics
    from boxie.observability.snapshot import operational_snapshot
    from boxie.persistence.database import database as telemetry_database
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    from sqlmodel import select

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, _, _):
            view = await life.submit(OWNER, "separate-pool", await upload(artifacts))
            async with telemetry_database(
                postgres_dsn,
                pool_size=1,
                max_overflow=0,
                pool_timeout=0.05,
            ) as telemetry:
                metrics = Metrics("api")
                metrics.refresh(await operational_snapshot(telemetry))
                async with telemetry.begin() as held:
                    await held.exec(select(1))
                    blocked = asyncio.create_task(operational_snapshot(telemetry))
                    assert (await life.get(OWNER, view.id)).state == SandboxState.ACCEPTED
                    assert (
                        await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
                    ).cleanup_pending
                    with pytest.raises(PoolTimeout):
                        await blocked
                    metrics.refresh_failed()
                    assert b"boxie_capacity_reserved 1.0" in metrics.render()
                metrics.refresh(await operational_snapshot(telemetry))
                assert b"boxie_cleanup_pending 1.0" in metrics.render()

    asyncio.run(exercise())


@pytest.mark.integration
@pytest.mark.parametrize("entrypoint", ["stop", "observation"])
def test_existing_unknown_cleanup_age_stays_unknown(
    postgres_dsn: str, tmp_path: Path, entrypoint: str
) -> None:
    from boxie.observability.snapshot import operational_snapshot
    from boxie.persistence.models import Sandbox

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            view = await life.submit(OWNER, "legacy-pending", await upload(artifacts))
            boot = uuid4()
            assert await life.bind_supervisor_boot(OWNER, config.identity, view.id, 1, None, boot)
            async with sessions() as session, session.begin():
                row = await session.get(Sandbox, view.id)
                assert row is not None
                row.state, row.cleanup_pending = "terminating", True
                row.cleanup_pending_since = None
            if entrypoint == "stop":
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            else:
                assert await life.record_observation(
                    SupervisorObservation(
                        view.id,
                        OWNER,
                        config.identity,
                        1,
                        boot,
                        1,
                        datetime.now(UTC),
                        SandboxState.TERMINATING,
                        True,
                        cleanup_pending=True,
                        reason=TerminationReason.REQUESTED,
                    )
                )
            sample = await operational_snapshot(sessions)
            assert sample.cleanup_pending == 1
            assert sample.cleanup_unknown == 1
            async with sessions() as session:
                row = await session.get(Sandbox, view.id)
                assert row is not None and row.cleanup_pending_since is None

    asyncio.run(exercise())


@pytest.mark.integration
def test_admission_retry_and_first_stop_preserve_origins(postgres_dsn: str, tmp_path: Path) -> None:
    from boxie.observability.telemetry.context import request_context
    from boxie.persistence.models import Outbox, Sandbox
    from sqlmodel import select

    async def exercise() -> None:
        creation, stop, retry = uuid4(), uuid4(), uuid4()
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            spec = await upload(artifacts)
            with request_context(str(creation)):
                view = await life.submit(OWNER, "origin", spec)
            with request_context(str(retry)):
                assert (await life.submit(OWNER, "origin", spec)).id == view.id
            with request_context(str(stop)):
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            with request_context(str(retry)):
                await life.terminate(OWNER, view.id, TerminationReason.PROVISIONING_FAILED)
            async with sessions() as session:
                row = await session.get(Sandbox, view.id)
                assert row is not None
                assert row.creation_request_id == creation
                assert row.stop_request_id == stop
                commands = (
                    await session.exec(select(Outbox).where(Outbox.sandbox_id == view.id))
                ).all()
                assert {command.kind: command.request_id for command in commands} == {
                    "start": creation,
                    "stop": stop,
                }

    asyncio.run(exercise())


@pytest.mark.integration
def test_coalesced_observation_keeps_distinct_action_origins(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from boxie.observability.telemetry.context import current_request_id, request_context
    from boxie.persistence.models import Outbox
    from sqlalchemy import delete

    async def exercise() -> None:
        creation, stop = uuid4(), uuid4()
        events: list[tuple[str, str | None]] = []
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            life.diagnostics = lambda event: events.append((event.event, current_request_id()))
            with request_context(str(creation)):
                view = await life.submit(OWNER, "coalesced", await upload(artifacts))
            with request_context(str(stop)):
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            async with sessions() as session, session.begin():
                await session.exec(delete(Outbox))
            boot = uuid4()
            await life.bind_supervisor_boot(OWNER, config.identity, view.id, 1, None, boot)
            with request_context(str(uuid4())):
                await life.record_observation(
                    SupervisorObservation(
                        view.id,
                        OWNER,
                        config.identity,
                        1,
                        boot,
                        1,
                        datetime.now(UTC),
                        SandboxState.TERMINATED,
                        False,
                        ready_at=datetime.now(UTC),
                        removal_confirmed=True,
                    )
                )
            assert ("provisioning.completed", str(creation)) in events
            assert ("sandbox.ready", str(creation)) in events
            assert ("cleanup.completed", str(stop)) in events

    asyncio.run(exercise())


def test_autonomous_reconciliation_drops_inherited_request(tmp_path: Path) -> None:
    from boxie.observability.telemetry.context import current_request_id, request_context
    from lifecycle_support import settled, setup

    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)
        origins: list[str | None] = []
        try:
            await settled(supervisor, request.ref, SandboxState.READY)
            supervisor.diagnostics = lambda event: origins.append(current_request_id())
            daemon.unreachable = True
            inherited = str(uuid4())
            with request_context(inherited):
                await supervisor.remove(request.ref, TerminationReason.REQUESTED)
                assert origins[-1] == inherited
                await supervisor.reconcile()
                assert origins[-1] is not None and origins[-1] != inherited
                assert current_request_id() == inherited
        finally:
            daemon.unreachable = False
            await supervisor.close()

    asyncio.run(exercise())


@pytest.mark.integration
@pytest.mark.parametrize(
    "reason", [TerminationReason.PROVISIONING_FAILED, TerminationReason.EXPIRED]
)
@pytest.mark.parametrize("removed", [False, True])
def test_autonomous_cleanup_commits_origin_before_termination(
    postgres_dsn: str, tmp_path: Path, reason: TerminationReason, removed: bool
) -> None:
    from boxie.observability.telemetry.context import current_request_id, request_context
    from boxie.persistence.models import Sandbox

    async def exercise() -> None:
        creation, autonomous, later = uuid4(), uuid4(), uuid4()
        events: list[tuple[str, str | None]] = []
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            life.diagnostics = lambda event: events.append((event.event, current_request_id()))
            with request_context(str(creation)):
                view = await life.submit(OWNER, "autonomous-origin", await upload(artifacts))
            boot = uuid4()
            await life.bind_supervisor_boot(OWNER, config.identity, view.id, 1, None, boot)
            expected = creation if reason == TerminationReason.PROVISIONING_FAILED else autonomous
            with request_context(str(autonomous)):
                await life.record_observation(
                    SupervisorObservation(
                        view.id,
                        OWNER,
                        config.identity,
                        1,
                        boot,
                        1,
                        datetime.now(UTC),
                        SandboxState.TERMINATED if removed else SandboxState.TERMINATING,
                        not removed,
                        cleanup_pending=not removed,
                        removal_confirmed=removed,
                        reason=reason,
                    )
                )
            with request_context(str(later)):
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            with request_context(str(uuid4())):
                await life.record_observation(
                    SupervisorObservation(
                        view.id,
                        OWNER,
                        config.identity,
                        1,
                        boot,
                        2,
                        datetime.now(UTC),
                        SandboxState.TERMINATED,
                        False,
                        removal_confirmed=True,
                        reason=reason,
                    )
                )
            async with sessions() as session:
                row = await session.get(Sandbox, view.id)
                assert row is not None and row.stop_request_id == expected
            assert ("cleanup.completed", str(expected)) in events

    asyncio.run(exercise())
