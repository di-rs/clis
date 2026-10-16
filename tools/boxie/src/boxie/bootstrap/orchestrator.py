"""The privileged service entry point; only this process receives a Docker socket."""

import argparse
import asyncio
import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from boxie.bootstrap.config import OrchestratorConfig, read_credential
from boxie.persistence.artifacts import ArtifactStore
from boxie.sandbox.docker import DockerBackend
from boxie.sandbox.server import create_orchestrator_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Boxie private sandbox orchestrator")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = OrchestratorConfig.from_file(args.config)

    async def run() -> None:
        app = create_orchestrator_app(
            config,
            DockerBackend(config.docker_timeout),
            ArtifactStore(config.data_dir / "artifacts"),
            lambda: datetime.now(UTC),
            time.monotonic,
            credential=read_credential(config.credential_file),
        )

        def reload_profiles() -> None:
            try:
                app.state.orchestrator.profiles.reload()
            except Exception:
                logging.getLogger(__name__).exception(
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
                    access_log=False,
                )
            )
            await server.serve()
        finally:
            loop.remove_signal_handler(signal.SIGHUP)

    asyncio.run(run())


if __name__ == "__main__":
    main()
