"""Explicit, checksum-verified test-server input; never resolve mutable defaults."""

# The fixture uses the real SQL authority, authenticated HTTP adapters and
# production worker. Only the agent's Docker port is substituted here.
import asyncio
import hashlib
import json
import os
import platform
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
from boxie.api.app import ApiConfig, create_app
from boxie.persistence.migrate import migrate
from boxie.persistence.outbox import PostgresOutbox
from boxie.persistence.postgres import PostgresLifecycle
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie.persistence.start_guard import PostgresStartGuard
from boxie.sandbox.durable_client import DurableAgentClient
from boxie.sandbox.durable_server import create_durable_agent_app
from boxie.sandbox.lifecycle import DurableAgent
from boxie.sandbox.reporting import ObservationReporter
from boxie.temporal.activities import LifecycleActivities
from boxie.temporal.dispatcher import OutboxDispatcher
from boxie_test_support.agent import configuration
from boxie_test_support.api import CONSUMER_AUTH, serve
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from temporalio.client import Client
from test_agent_lifecycle import Daemon
from test_postgres import MIGRATIONS

VARIANTS = {("Darwin", "arm64"): "macOS_arm64", ("Linux", "x86_64"): "linux_amd64"}
PINS = json.loads((Path(__file__).with_name("test-server-pins.json")).read_text())["artifacts"]


def test_server_path() -> Path:
    path = Path(os.environ.get("BOXIE_TEST_SERVER", "/missing/BOXIE_TEST_SERVER"))
    variant = VARIANTS.get((platform.system(), platform.machine()))
    expected = next((entry["binary_sha256"] for entry in PINS if entry["variant"] == variant), None)
    if expected is None or not path.is_file():
        raise RuntimeError(
            "set BOXIE_TEST_SERVER to the pinned 1.40.0 executable for this platform"
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("test server executable checksum differs from pinned 1.40.0")
    return path


@asynccontextmanager
async def durable_stack(dsn: str, root: Path, client: Client, queue: str) -> AsyncIterator[Any]:
    await asyncio.to_thread(migrate, dsn, MIGRATIONS)
    config = configuration(root)
    profiles = json.loads(config.profiles_file.read_text())
    profiles["shell"]["env"]["PRIVATE"] = ["SECRET-ENV-SNAPSHOT"]
    profiles["shell"]["commands"].append(["/bin/sh", "-c", "SECRET-COMMAND"])
    config.profiles_file.write_text(json.dumps(profiles))
    daemon = Daemon()
    async with AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]](
        dsn, min_size=1, max_size=20, kwargs={"row_factory": dict_row}
    ) as pool:
        artifacts = PostgresArtifactStore(pool, root / "sql-artifacts")
        agent = DurableAgent(
            config,
            daemon,
            artifacts.path,
            PostgresStartGuard(pool),
            lambda: datetime.now(UTC),
            time.monotonic,
        )
        reporter = ObservationReporter("http://127.0.0.1:1", b"agent-test-secret")
        app = create_durable_agent_app(
            agent, artifacts, credential=b"agent-test-secret", reporter=reporter
        )
        async with (
            serve(app) as agent_url,
            DurableAgentClient(agent_url, b"agent-test-secret", timeout=2) as control,
        ):
            life = PostgresLifecycle(pool, control, config.identity, control.ready)
            api = create_app(
                ApiConfig("test", {"one": b"consumer-one-secret"}),
                life,
                control,
                control,
                observations=life,
                agent_credential=b"agent-test-secret",
            )
            async with (
                serve(api) as url,
                httpx.AsyncClient(base_url=url, headers=CONSUMER_AUTH) as http,
            ):
                reporter.client.base_url = httpx.URL(url)
                activities = LifecycleActivities(life, control)
                dispatcher = OutboxDispatcher(PostgresOutbox(pool), client, queue)
                yield http, life, agent, daemon, pool, activities, dispatcher
