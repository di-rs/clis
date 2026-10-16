"""Earliest process-owned logging and Uvicorn startup; factories stay embeddable."""

import asyncio
import os

from service_logging.logging import install_asyncio_handler, run_process
from service_logging.metrics import MetricsListener

from playground.observability import LogSettings, Metrics, bootstrap_logging


def main() -> None:
    runtime = bootstrap_logging(
        LogSettings(
            service="cli-playground",
            role="gateway",
            release=os.environ.get("PLAYGROUND_RELEASE", "unknown"),
        )
    )

    metrics = Metrics(runtime)
    listener = MetricsListener(
        metrics, os.environ.get("PLAYGROUND_METRICS_HOST", "127.0.0.1"), 9104
    )

    def run() -> None:
        listener.start()
        import uvicorn

        from playground.app import create_app

        async def serve() -> None:
            install_asyncio_handler(runtime)
            app = create_app(metrics=metrics)
            await uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="0.0.0.0",
                    port=8000,
                    workers=1,
                    ws_max_size=65536,
                    proxy_headers=False,
                    log_config=None,
                    access_log=False,
                )
            ).serve()

        asyncio.run(serve())

    run_process(runtime, run, close_metrics=listener.close)


if __name__ == "__main__":
    main()
