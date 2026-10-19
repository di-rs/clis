"""Activity success must prove the final journal write succeeded after the last external RPC."""

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from boxie.domain.lifecycle import OrchestratorObservation
from boxie.domain.models import SandboxState, TerminationReason
from boxie.persistence.start_guard import PostgresStartGuard
from boxie.sandbox.execution.orchestrator import Orchestrator
from boxie.temporal.activities import LifecycleActivities
from boxie_test_support.dependencies import postgres_dsn as postgres_dsn
from boxie_test_support.dependencies import postgres_server as postgres_server
from boxie_test_support.execution import Daemon
from database_support import OWNER, counts, database, upload
from temporalio.exceptions import ApplicationError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("completion", ["remove_sandbox", "load_request"])
def test_cleanup_completion_waits_for_sessions_before_forgetting_journal(
    postgres_dsn: str, tmp_path: Path, completion: str
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            sandbox = await life.submit(OWNER, "cleanup-completion", await upload(artifacts))
            daemon = Daemon()
            orchestrator = Orchestrator(
                config,
                daemon,
                artifacts.path,
                PostgresStartGuard(sessions),
                lambda: datetime.now(UTC),
                time.monotonic,
            )
            activities = LifecycleActivities(life, orchestrator)
            finish = getattr(activities, completion)
            try:
                await activities.ensure_sandbox(str(sandbox.id))
                record = orchestrator.records[sandbox.id]
                with orchestrator.terminal_session(record.ref):
                    await life.terminate(OWNER, sandbox.id, TerminationReason.REQUESTED)
                    if completion == "load_request":
                        assert await life.record_observation(
                            await orchestrator.remove(record.ref, TerminationReason.REQUESTED)
                        )
                    with pytest.raises(ApplicationError, match="cleanup pending"):
                        await finish(str(sandbox.id))
                    assert (await counts(sessions))[1:3] == (0, 0)
                    assert not daemon.resources
                    assert sandbox.id in orchestrator.records
                    assert len(json.loads(orchestrator.journal.path.read_bytes())["records"]) == 1
                await finish(str(sandbox.id))
                assert not orchestrator.records and not orchestrator.runtimes
                assert json.loads(orchestrator.journal.path.read_bytes())["records"] == []
                await finish(str(sandbox.id))
                assert daemon.creates == 1
            finally:
                await orchestrator.close()

    asyncio.run(exercise())


def test_removal_activity_retries_until_terminal_report_is_persisted(
    postgres_dsn: str, tmp_path: Path
) -> None:
    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            sandbox = await life.submit(OWNER, "stale-removal", await upload(artifacts))
            await life.terminate(OWNER, sandbox.id, TerminationReason.REQUESTED)
            boot = uuid4()
            assert await life.bind_orchestrator_boot(
                OWNER, config.identity, sandbox.id, 1, None, boot
            )
            pending = OrchestratorObservation(
                sandbox.id,
                OWNER,
                config.identity,
                1,
                boot,
                2,
                datetime.now(UTC),
                SandboxState.TERMINATING,
                True,
                cleanup_pending=True,
            )
            assert await life.record_observation(pending)
            stale = replace(
                pending,
                event_version=1,
                state=SandboxState.TERMINATED,
                resource_present=False,
                cleanup_pending=False,
                removal_confirmed=True,
            )
            control = SimpleNamespace(
                boot_id=boot,
                config=config,
                records={},
                initialize=AsyncMock(),
                remove=AsyncMock(side_effect=[stale, replace(stale, event_version=3)]),
            )
            activities = LifecycleActivities(life, cast(Orchestrator, control))
            with pytest.raises(ApplicationError, match="cleanup pending"):
                await activities.remove_sandbox(str(sandbox.id))
            assert await counts(sessions) == (1, 1, 1, 2)
            await activities.remove_sandbox(str(sandbox.id))
            assert await counts(sessions) == (1, 0, 0, 2)

    asyncio.run(exercise())


def test_cleanup_observations_do_not_grow_workflow_notifications(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from boxie.persistence.models import Outbox
    from sqlalchemy import func
    from sqlmodel import col, select

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, config):
            sandbox = await life.submit(OWNER, "cleanup-notifications", await upload(artifacts))
            boot = uuid4()
            assert await life.bind_orchestrator_boot(
                OWNER, config.identity, sandbox.id, 1, None, boot
            )
            await life.terminate(OWNER, sandbox.id, TerminationReason.REQUESTED)
            for version in range(1, 5):
                assert await life.record_observation(
                    OrchestratorObservation(
                        sandbox.id,
                        OWNER,
                        config.identity,
                        1,
                        boot,
                        version,
                        datetime.now(UTC),
                        SandboxState.TERMINATING,
                        True,
                        cleanup_pending=True,
                    )
                )
            async with sessions() as session:
                commands = (await session.exec(select(func.count(col(Outbox.id))))).one()
            assert commands == 2  # Admission + SQL stop commit; cleanup retries need no signals.
            assert await counts(sessions) == (1, 1, 1, 2)
            assert (await life.get(OWNER, sandbox.id)).cleanup_pending

    asyncio.run(exercise())
