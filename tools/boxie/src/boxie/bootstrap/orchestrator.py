"""The privileged service entry point; only this process receives a Docker socket."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from boxie.bootstrap.config import OrchestratorConfig, read_credential
from boxie.bootstrap.dependencies import database_for, temporal_client
from boxie.persistence.postgres_artifacts import PostgresArtifactStore

if TYPE_CHECKING:
    from fastapi import FastAPI

    from boxie.observability.metrics import Metrics
    from boxie.sandbox.execution.orchestrator import Orchestrator


def create_orchestrator_app(
    orchestrator: Orchestrator,
    artifacts: PostgresArtifactStore,
    *,
    credential: bytes,
    runtime: Callable[[], AbstractAsyncContextManager[None]] | None = None,
    request_completed: Callable[[str, str, str, float], None] | None = None,
) -> FastAPI:
    """Compose process services; the HTTP adapter receives only its required ports."""

    from sqlmodel import select

    from boxie.observability.logging import RequestLogging
    from boxie.sandbox.transport.server import create_orchestrator_app as create_transport_app

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
        try:
            async with runtime() if runtime is not None else nullcontext():
                yield
        finally:
            await orchestrator.close()

    app = create_transport_app(
        orchestrator, artifacts, credential=credential, readiness=readiness, lifespan=lifespan
    )
    app.add_middleware(RequestLogging, completed=request_completed)
    return app


def _main(metrics: Metrics) -> None:
    import uvicorn

    from boxie.bootstrap.runtime import embedded_workers
    from boxie.observability.runtime import (
        background,
        orchestrator_cache,
        orchestrator_dependencies,
    )
    from boxie.observability.telemetry.logging import install_asyncio_handler, log_runtime
    from boxie.persistence.postgres import PostgresLifecycle
    from boxie.persistence.start_guard import PostgresStartGuard
    from boxie.sandbox.docker import DockerBackend
    from boxie.sandbox.execution.orchestrator import Orchestrator
    from boxie.sandbox.execution.ownership import acquire_owner_lock

    parser = argparse.ArgumentParser(description="Boxie private sandbox orchestrator")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = OrchestratorConfig.from_file(args.config)

    if config.lifecycle is None:
        raise ValueError("orchestrator requires PostgreSQL and Temporal lifecycle configuration")
    lifecycle = config.lifecycle
    dsn_file = lifecycle.postgres_dsn_file

    async def run() -> None:
        install_asyncio_handler(log_runtime())
        with acquire_owner_lock(config.data_dir):
            await run_owned()

    async def run_owned() -> None:
        async with database_for(dsn_file) as sessions:
            artifacts = PostgresArtifactStore(sessions, config.data_dir / "artifacts")
            orchestrator = Orchestrator(
                config,
                DockerBackend(config.docker_timeout),
                artifacts.path,
                PostgresStartGuard(sessions),
                lambda: datetime.now(UTC),
                time.monotonic,
                diagnostics=metrics.transition,
            )

            async def local_ready() -> bool:
                return orchestrator.execution_ready

            repository = PostgresLifecycle(
                sessions, orchestrator, config.identity, local_ready, diagnostics=metrics.transition
            )
            client = await temporal_client(lifecycle)
            credential = read_credential(config.credential_file)
            app = create_orchestrator_app(
                orchestrator,
                artifacts,
                credential=credential,
                request_completed=metrics.http_completed,
                runtime=lambda: embedded_workers(client, repository, orchestrator, artifacts),
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
                        log_config=None,
                    )
                )
                async with background(
                    orchestrator_cache(metrics, orchestrator),
                    orchestrator_dependencies(metrics, orchestrator, sessions),
                ):
                    await server.serve()
            finally:
                loop.remove_signal_handler(signal.SIGHUP)

    asyncio.run(run())


def main() -> None:
    from boxie.observability.logging import process_logging

    logs = process_logging("orchestrator")
    from boxie.observability.runtime import run_metrics_process

    run_metrics_process(logs, "orchestrator", _main)


if __name__ == "__main__":
    main()
