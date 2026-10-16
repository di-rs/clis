"""The privileged service entry point; only this process receives a Docker socket."""

import argparse
import asyncio
import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from service_logging.logging import install_asyncio_handler, log_runtime

if TYPE_CHECKING:
    from boxie.observability.metrics import Metrics


def _main(metrics: Metrics) -> None:
    import uvicorn

    from boxie.bootstrap.config import AgentConfig, read_credential
    from boxie.bootstrap.durable import pool_for
    from boxie.observability.logging import RequestLogging
    from boxie.observability.runtime import (
        agent_cache,
        agent_dependencies,
        background,
    )
    from boxie.persistence.postgres_artifacts import PostgresArtifactStore
    from boxie.persistence.start_guard import PostgresStartGuard
    from boxie.sandbox.docker import DockerBackend
    from boxie.sandbox.durable_server import create_durable_agent_app
    from boxie.sandbox.lifecycle import DurableAgent
    from boxie.sandbox.reporting import ObservationReporter

    parser = argparse.ArgumentParser(description="Boxie private sandbox agent")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = AgentConfig.from_file(args.config)

    if config.postgres_dsn_file is None or config.api_url is None:
        raise ValueError("durable agent requires postgres_dsn_file and api_url")
    dsn_file, api_url = config.postgres_dsn_file, config.api_url

    async def run() -> None:
        install_asyncio_handler(log_runtime())
        async with pool_for(dsn_file) as pool:
            artifacts = PostgresArtifactStore(pool, config.data_dir / "artifacts")
            agent = DurableAgent(
                config,
                DockerBackend(config.docker_timeout),
                artifacts.path,
                PostgresStartGuard(pool),
                lambda: datetime.now(UTC),
                time.monotonic,
            )
            agent.diagnostics = metrics.transition
            credential = read_credential(config.credential_file)
            app = create_durable_agent_app(
                agent,
                artifacts,
                credential=credential,
                reporter=ObservationReporter(api_url, credential),
            )

            app.add_middleware(RequestLogging, completed=metrics.http_completed)

            def reload_profiles() -> None:
                try:
                    agent.profiles.reload()
                except Exception:
                    logging.getLogger(__name__).error(
                        "profile reload rejected; retaining approved profiles"
                    )

            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGHUP, reload_profiles)
            try:
                server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host=config.bind_host,
                        port=config.bind_port,
                        ws="websockets-sansio",
                        ws_max_size=65536,
                        ws_max_queue=4,
                        ws_per_message_deflate=False,
                        access_log=False,
                        log_config=None,
                    )
                )
                async with background(
                    agent_cache(metrics, agent), agent_dependencies(metrics, agent, pool)
                ):
                    await server.serve()
            finally:
                loop.remove_signal_handler(signal.SIGHUP)

    asyncio.run(run())


def main() -> None:
    from boxie.observability.logging import process_logging

    runtime = process_logging("agent")
    from boxie.observability.runtime import run_metrics_process

    run_metrics_process(runtime, "agent", _main)


if __name__ == "__main__":
    main()
