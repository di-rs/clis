"""The privileged service entry point; only this process receives a Docker socket."""

import argparse
import asyncio
import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from boxie.bootstrap.config import AgentConfig, read_credential
from boxie.bootstrap.durable import pool_for
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie.persistence.start_guard import PostgresStartGuard
from boxie.sandbox.docker import DockerBackend
from boxie.sandbox.durable_server import create_durable_agent_app
from boxie.sandbox.lifecycle import DurableAgent
from boxie.sandbox.reporting import ObservationReporter


def main() -> None:
    parser = argparse.ArgumentParser(description="Boxie private sandbox agent")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = AgentConfig.from_file(args.config)

    if config.postgres_dsn_file is None or config.api_url is None:
        raise ValueError("durable agent requires postgres_dsn_file and api_url")
    dsn_file, api_url = config.postgres_dsn_file, config.api_url

    async def run() -> None:
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
            credential = read_credential(config.credential_file)
            app = create_durable_agent_app(
                agent,
                artifacts,
                credential=credential,
                reporter=ObservationReporter(api_url, credential),
            )

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
                    )
                )
                await server.serve()
            finally:
                loop.remove_signal_handler(signal.SIGHUP)

    asyncio.run(run())


if __name__ == "__main__":
    main()
