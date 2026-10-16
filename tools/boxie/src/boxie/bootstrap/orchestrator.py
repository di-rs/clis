"""The privileged service entry point; only this process receives a Docker socket."""

import argparse
import asyncio
import logging
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from sqlmodel import select

from boxie.bootstrap.config import OrchestratorConfig, read_credential
from boxie.bootstrap.durable import database_for
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie.persistence.start_guard import PostgresStartGuard
from boxie.sandbox.docker import DockerBackend
from boxie.sandbox.execution.orchestrator import DurableOrchestrator
from boxie.sandbox.transport.reporting import ObservationReporter
from boxie.sandbox.transport.server import create_orchestrator_app as create_transport_app


def create_orchestrator_app(
    orchestrator: DurableOrchestrator,
    artifacts: PostgresArtifactStore,
    *,
    credential: bytes,
    reporter: ObservationReporter,
) -> FastAPI:
    """Compose process services; the HTTP adapter receives only its required ports."""
    orchestrator.observer = reporter.offer
    orchestrator.forget_observation = reporter.forget

    async def collect() -> None:
        while True:
            try:
                await artifacts.collect()
                await artifacts.collect_orphans()
            except Exception:
                pass  # Retain unresolved collection intents for retry.
            await asyncio.sleep(30)

    async def readiness() -> dict[str, bool]:
        try:
            async with asyncio.timeout(5):
                docker_ready = await orchestrator.docker.ready()
                async with artifacts.sessions() as session:
                    await session.exec(select(1))
                sql_ready = True
        except Exception:
            docker_ready = sql_ready = False
        return {
            "docker": docker_ready,
            "postgres": sql_ready,
            "execution": orchestrator.execution_ready,
        }

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await orchestrator.start()
        reporting = asyncio.create_task(reporter.run())
        collection = asyncio.create_task(collect())
        try:
            yield
        finally:
            try:
                await orchestrator.close()
            finally:
                reporting.cancel()
                collection.cancel()
                await asyncio.gather(reporting, collection, return_exceptions=True)
                await reporter.close()

    return create_transport_app(
        orchestrator, artifacts, credential=credential, readiness=readiness, lifespan=lifespan
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Boxie private sandbox orchestrator")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = OrchestratorConfig.from_file(args.config)

    if config.postgres_dsn_file is None or config.api_url is None:
        raise ValueError("durable orchestrator requires postgres_dsn_file and api_url")
    dsn_file, api_url = config.postgres_dsn_file, config.api_url

    async def run() -> None:
        async with database_for(dsn_file) as sessions:
            artifacts = PostgresArtifactStore(sessions, config.data_dir / "artifacts")
            orchestrator = DurableOrchestrator(
                config,
                DockerBackend(config.docker_timeout),
                artifacts.path,
                PostgresStartGuard(sessions),
                lambda: datetime.now(UTC),
                time.monotonic,
            )
            credential = read_credential(config.credential_file)
            app = create_orchestrator_app(
                orchestrator,
                artifacts,
                credential=credential,
                reporter=ObservationReporter(api_url, credential),
            )

            def reload_profiles() -> None:
                try:
                    orchestrator.profiles.reload()
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
