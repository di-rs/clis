"""Quiet container probes; credentials stay in private files and HTTP headers."""

import argparse
import asyncio
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from boxie.bootstrap.config import read_credential


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http_ready(url: str, credential: Path) -> bool:
    request = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + read_credential(credential).decode()}
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=7) as response:
            payload = response.read(4097)
        body = json.loads(payload) if len(payload) <= 4096 else {}
        dependencies = body.get("dependencies", {})
        return bool(dependencies) and all(value is True for value in dependencies.values())
    except OSError, ValueError, AttributeError:
        return False


async def worker_ready(settings: dict[str, Any]) -> bool:
    from boxie.bootstrap.durable import Dependencies, durable_config, pool_for, temporal_client
    from boxie.sandbox.durable_client import DurableAgentClient

    async with asyncio.timeout(8):
        config = durable_config(settings["durable"])
        client = await temporal_client(config)
        async with (
            pool_for(config.postgres_dsn_file) as pool,
            DurableAgentClient(
                settings["agent_url"],
                read_credential(Path(settings["agent_credential_file"])),
                timeout=5,
            ) as agent,
        ):
            async with pool.connection() as connection:
                cursor = await connection.execute("SELECT version, checksum FROM schema_migrations")
                rows = await cursor.fetchall()
            if not schema_current(
                {row["version"]: row["checksum"] for row in rows},
                Path(__file__).parents[1] / "persistence/migrations",
            ):
                return False
            return await Dependencies(agent, client)()


def schema_current(applied: dict[int, str], directory: Path) -> bool:
    expected = {
        int(path.name.partition("_")[0]): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.glob("[0-9]*_*.sql")
    }
    return bool(expected) and applied == expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "agent", "worker"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = json.loads(args.config.read_text())
        if args.role == "worker":
            ready = asyncio.run(worker_ready(settings))
        else:
            port = settings.get("bind_port", 8090 if args.role == "api" else 8091)
            credential = (
                next(iter(settings["consumer_credential_files"].values()))
                if args.role == "api"
                else settings["credential_file"]
            )
            ready = http_ready(f"http://127.0.0.1:{port}/readyz", Path(credential))
    except Exception:
        ready = False
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
