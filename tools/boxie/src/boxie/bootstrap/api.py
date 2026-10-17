"""Unprivileged API composition root: secrets and orchestrator origin are operator-owned."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from boxie.api.app import ApiConfig
    from boxie.bootstrap.config import LifecycleConfig
    from boxie.domain.models import OrchestratorIdentity
    from boxie.observability.metrics import Metrics


@dataclass(frozen=True)
class RuntimeConfig:
    api: ApiConfig
    orchestrator_url: str
    orchestrator_credential: bytes = field(repr=False)
    lifecycle: LifecycleConfig
    identity: OrchestratorIdentity
    bind_host: str = "127.0.0.1"
    bind_port: int = 8090
    request_timeout: float = 20


def load_configuration(path: Path) -> RuntimeConfig:
    from pydantic import TypeAdapter, ValidationError

    from boxie.api.app import ApiConfig
    from boxie.bootstrap.config import read_credential
    from boxie.bootstrap.dependencies import lifecycle_config, orchestrator_identity
    from boxie.contracts.common import ConsumerId

    data = json.loads(path.read_text())
    orchestrator = read_credential(Path(data.pop("orchestrator_credential_file")))
    consumers = {
        consumer: read_credential(Path(secret))
        for consumer, secret in data.pop("consumer_credential_files").items()
    }
    values = list(consumers.values())
    if any(hmac.compare_digest(orchestrator, value) for value in values) or len(set(values)) != len(
        values
    ):
        raise ValueError("orchestrator and consumer credentials must be distinct")
    # Consumer IDs obey the same wire constraint as the orchestrator's trusted identity field.
    for consumer in consumers:
        try:
            TypeAdapter(ConsumerId).validate_python(consumer)
        except ValidationError:
            raise ValueError("invalid consumer ID") from None
    api = ApiConfig(data.pop("release"), consumers)
    if "lifecycle" not in data or "identity" not in data:
        raise ValueError("lifecycle configuration and orchestrator identity required")
    data["lifecycle"] = lifecycle_config(data.pop("lifecycle"))
    data["identity"] = orchestrator_identity(data.pop("identity"))
    return RuntimeConfig(api=api, orchestrator_credential=orchestrator, **data)


def _main(metrics: Metrics) -> None:
    import uvicorn

    from boxie.api.app import create_app
    from boxie.bootstrap.dependencies import Dependencies, database_for, temporal_client
    from boxie.bootstrap.schedules import check_schedules
    from boxie.observability.runtime import api_dependencies, background
    from boxie.observability.snapshot import refresh_snapshots
    from boxie.observability.telemetry.logging import install_asyncio_handler, log_runtime
    from boxie.persistence.outbox import PostgresOutbox
    from boxie.persistence.postgres import PostgresLifecycle
    from boxie.sandbox.transport.client import OrchestratorClient
    from boxie.temporal.contracts import lifecycle_queue
    from boxie.temporal.dispatcher import CommandDelivery

    parser = argparse.ArgumentParser(description="Boxie private sandbox API")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_configuration(args.config)

    async def run() -> None:
        install_asyncio_handler(log_runtime())
        # SQL-backed retry/status/stop and observations must remain reachable
        # during Temporal outages. Readiness connects with a bounded timeout;
        # only new admission depends on that probe. Worker startup stays eager.
        temporal = await temporal_client(config.lifecycle, lazy=True)
        async with (
            database_for(config.lifecycle.postgres_dsn_file) as sessions,
            OrchestratorClient(
                config.orchestrator_url,
                config.orchestrator_credential,
                timeout=config.request_timeout,
            ) as orchestrator,
        ):
            lifecycle = PostgresLifecycle(
                sessions,
                orchestrator,
                config.identity,
                Dependencies(
                    orchestrator,
                    temporal,
                    config.identity,
                    lambda: check_schedules(temporal, config.identity),
                ),
                diagnostics=metrics.transition,
            )
            app = create_app(
                config.api,
                lifecycle,
                orchestrator,
                orchestrator,
                request_completed=metrics.http_completed,
                deliver=CommandDelivery(
                    PostgresOutbox(sessions, config.identity),
                    temporal,
                    lifecycle_queue(config.identity),
                ).deliver_pending,
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=config.bind_host,
                    port=config.bind_port,
                    ws="websockets-sansio",
                    ws_max_size=16384,
                    ws_max_queue=4,
                    ws_per_message_deflate=False,
                    access_log=False,
                    log_config=None,
                )
            )
            async with background(
                refresh_snapshots(metrics, config.lifecycle.postgres_dsn_file),
                api_dependencies(metrics, orchestrator, temporal, config.identity),
            ):
                await server.serve()

    asyncio.run(run())


def main() -> None:
    from boxie.observability.logging import process_logging

    logs = process_logging("api")
    from boxie.observability.runtime import run_metrics_process

    run_metrics_process(logs, "api", _main)


if __name__ == "__main__":
    main()
