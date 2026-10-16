"""Real SQL, Temporal and HTTP with orchestrator-owned embedded workers."""

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
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
from boxie.sandbox.execution.orchestrator import Orchestrator
from boxie.sandbox.transport.client import OrchestratorClient
from boxie.temporal.activities import LifecycleActivities
from boxie.temporal.contracts import lifecycle_queue
from boxie.temporal.dispatcher import CommandDelivery
from boxie_test_support.api import CONSUMER_AUTH, serve
from boxie_test_support.execution import Daemon
from boxie_test_support.orchestrator import configuration
from temporalio.client import Client
from temporalio.service import TLSConfig


@asynccontextmanager
async def lifecycle_stack(dsn: str, root: Path, client: Client) -> AsyncIterator[Any]:
    migrate(dsn)
    config = configuration(root)
    profiles = json.loads(config.profiles_file.read_text())
    profiles["shell"]["commands"].append(["/bin/sh", "-c", "SECRET-COMMAND"])
    profiles["shell"]["env"]["PRIVATE"] = ["SECRET-ENV-SNAPSHOT"]
    config.profiles_file.write_text(json.dumps(profiles))
    daemon = Daemon()
    async with database(dsn) as sessions:
        artifacts = PostgresArtifactStore(sessions, root / "artifacts")
        orchestrator = Orchestrator(
            config,
            daemon,
            artifacts.path,
            PostgresStartGuard(sessions),
            lambda: datetime.now(UTC),
            time.monotonic,
        )
        app = create_orchestrator_app(
            orchestrator, artifacts, credential=b"orchestrator-test-secret"
        )
        async with (
            serve(app) as origin,
            OrchestratorClient(origin, b"orchestrator-test-secret") as control,
        ):
            life = PostgresLifecycle(sessions, control, config.identity, control.ready)
            delivery = CommandDelivery(
                PostgresOutbox(sessions), client, lifecycle_queue(config.identity)
            )
            api = create_app(
                ApiConfig("test", {"one": b"consumer-one-secret"}),
                life,
                control,
                control,
                deliver=delivery.deliver_pending,
            )
            async with (
                serve(api) as url,
                httpx.AsyncClient(base_url=url, headers=CONSUMER_AUTH) as http,
            ):
                yield SimpleNamespace(
                    http=http,
                    life=life,
                    orchestrator=orchestrator,
                    daemon=daemon,
                    sessions=sessions,
                    artifacts=artifacts,
                    delivery=delivery,
                    activities=LifecycleActivities(life, orchestrator),
                )


def tls(stack: Any, identity: str, *, domain: str = "temporal") -> TLSConfig:
    directory = (
        "operator" if identity == "operator" else "ui" if identity == "ui-client" else "clients"
    )
    return TLSConfig(
        server_root_ca_cert=(stack.root / "clients/frontend-ca.pem").read_bytes(),
        domain=domain,
        client_cert=(stack.root / directory / f"{identity}.pem").read_bytes(),
        client_private_key=(stack.root / directory / f"{identity}.key").read_bytes(),
    )
