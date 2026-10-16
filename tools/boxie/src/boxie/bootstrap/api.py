"""Unprivileged API composition root: secrets and agent origin are operator-owned."""

import argparse
import asyncio
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from service_telemetry.logging import install_asyncio_handler, log_runtime

if TYPE_CHECKING:
    from boxie.api.app import ApiConfig
    from boxie.bootstrap.config import DurableConfig
    from boxie.domain.models import AgentIdentity
    from boxie.observability.metrics import Metrics


@dataclass(frozen=True)
class RuntimeConfig:
    api: ApiConfig
    agent_url: str
    agent_credential: bytes = field(repr=False)
    durable: DurableConfig
    identity: AgentIdentity
    bind_host: str = "127.0.0.1"
    bind_port: int = 8090
    request_timeout: float = 20


def load_configuration(path: Path) -> RuntimeConfig:
    from pydantic import TypeAdapter, ValidationError

    from boxie.api.app import ApiConfig
    from boxie.bootstrap.config import read_credential
    from boxie.bootstrap.durable import agent_identity, durable_config
    from boxie.sandbox.protocol import ConsumerId

    data = json.loads(path.read_text())
    agent = read_credential(Path(data.pop("agent_credential_file")))
    consumers = {
        consumer: read_credential(Path(secret))
        for consumer, secret in data.pop("consumer_credential_files").items()
    }
    values = list(consumers.values())
    if any(hmac.compare_digest(agent, value) for value in values) or len(set(values)) != len(
        values
    ):
        raise ValueError("agent and consumer credentials must be distinct")
    # Consumer IDs obey the same wire constraint as the agent's trusted identity field.
    for consumer in consumers:
        try:
            TypeAdapter(ConsumerId).validate_python(consumer)
        except ValidationError:
            raise ValueError("invalid consumer ID") from None
    api = ApiConfig(data.pop("release"), consumers)
    if "durable" not in data or "identity" not in data:
        raise ValueError("durable configuration and agent identity required")
    data["durable"] = durable_config(data.pop("durable"))
    data["identity"] = agent_identity(data.pop("identity"))
    return RuntimeConfig(api=api, agent_credential=agent, **data)


def _main(metrics: Metrics) -> None:
    import uvicorn

    from boxie.api.app import create_app
    from boxie.bootstrap.durable import Dependencies, pool_for, temporal_client
    from boxie.observability.runtime import api_dependencies, background
    from boxie.observability.snapshot import refresh_snapshots
    from boxie.persistence.postgres import PostgresLifecycle
    from boxie.sandbox.durable_client import DurableAgentClient

    parser = argparse.ArgumentParser(description="Boxie private sandbox API")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_configuration(args.config)

    async def run() -> None:
        install_asyncio_handler(log_runtime())
        # SQL-backed retry/status/stop and observations must remain reachable
        # during Temporal outages. Readiness connects with a bounded timeout;
        # only new admission depends on that probe. Worker startup stays eager.
        temporal = await temporal_client(config.durable, lazy=True)
        async with (
            pool_for(config.durable.postgres_dsn_file) as pool,
            DurableAgentClient(
                config.agent_url, config.agent_credential, timeout=config.request_timeout
            ) as agent,
        ):
            lifecycle = PostgresLifecycle(
                pool,
                agent,
                config.identity,
                Dependencies(agent, temporal),
                diagnostics=metrics.transition,
            )
            app = create_app(
                config.api,
                lifecycle,
                agent,
                agent,
                observations=lifecycle,
                agent_credential=config.agent_credential,
                request_completed=metrics.http_completed,
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
                refresh_snapshots(metrics, config.durable.postgres_dsn_file),
                api_dependencies(metrics, agent, temporal),
            ):
                await server.serve()

    asyncio.run(run())


def main() -> None:
    from boxie.observability.logging import process_logging

    runtime = process_logging("api")
    from boxie.observability.runtime import run_metrics_process

    run_metrics_process(runtime, "api", _main)


if __name__ == "__main__":
    main()
