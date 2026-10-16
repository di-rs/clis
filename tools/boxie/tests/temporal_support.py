"""Real SQL and authenticated HTTP adapters for the production Temporal worker."""

# The fixture uses the real SQL authority, authenticated HTTP adapters and
# production worker. Only the orchestrator's Docker port is substituted here.
import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from boxie.api.app import ApiConfig, create_app
from boxie.bootstrap.orchestrator import create_orchestrator_app
from boxie.persistence.database import database
from boxie.persistence.migrate import migrate
from boxie.persistence.outbox import PostgresOutbox
from boxie.persistence.postgres import PostgresLifecycle
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie.persistence.start_guard import PostgresStartGuard
from boxie.sandbox.execution.orchestrator import DurableOrchestrator
from boxie.sandbox.transport.client import OrchestratorClient
from boxie.sandbox.transport.reporting import ObservationReporter
from boxie.temporal.activities import LifecycleActivities
from boxie.temporal.dispatcher import OutboxDispatcher
from boxie_test_support.api import CONSUMER_AUTH, serve
from boxie_test_support.orchestrator import configuration
from temporal_test_server import test_server_path as test_server_path
from temporalio.client import Client
from test_orchestrator_lifecycle import Daemon
from test_postgres import MIGRATIONS


@asynccontextmanager
async def durable_stack(dsn: str, root: Path, client: Client, queue: str) -> AsyncIterator[Any]:
    await asyncio.to_thread(migrate, dsn, MIGRATIONS)
    config = configuration(root)
    profiles = json.loads(config.profiles_file.read_text())
    profiles["shell"]["env"]["PRIVATE"] = ["SECRET-ENV-SNAPSHOT"]
    profiles["shell"]["commands"].append(["/bin/sh", "-c", "SECRET-COMMAND"])
    config.profiles_file.write_text(json.dumps(profiles))
    daemon = Daemon()
    async with database(dsn, pool_size=20) as sessions:
        artifacts = PostgresArtifactStore(sessions, root / "sql-artifacts")
        orchestrator = DurableOrchestrator(
            config,
            daemon,
            artifacts.path,
            PostgresStartGuard(sessions),
            lambda: datetime.now(UTC),
            time.monotonic,
        )
        reporter = ObservationReporter("http://127.0.0.1:1", b"orchestrator-test-secret")
        app = create_orchestrator_app(
            orchestrator, artifacts, credential=b"orchestrator-test-secret", reporter=reporter
        )
        async with (
            serve(app) as orchestrator_url,
            OrchestratorClient(orchestrator_url, b"orchestrator-test-secret", timeout=2) as control,
        ):
            life = PostgresLifecycle(sessions, control, config.identity, control.ready)
            api = create_app(
                ApiConfig("test", {"one": b"consumer-one-secret"}),
                life,
                control,
                control,
                observations=life,
                orchestrator_credential=b"orchestrator-test-secret",
            )
            async with (
                serve(api) as url,
                httpx.AsyncClient(base_url=url, headers=CONSUMER_AUTH) as http,
            ):
                reporter.client.base_url = httpx.URL(url)
                activities = LifecycleActivities(life, control)
                dispatcher = OutboxDispatcher(PostgresOutbox(sessions), client, queue)
                yield http, life, orchestrator, daemon, sessions, activities, dispatcher
