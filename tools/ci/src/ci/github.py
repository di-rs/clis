"""Bounded GitHub API access shared by benchmark and playground publishers."""

import json
import os
import selectors
import subprocess
import tempfile
import time
from typing import cast

MAX_JSON = 2 * 1024 * 1024


def table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return cast(dict[str, object], value)


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return cast(list[object], value)


class PaginationLimit(ValueError):
    """A bounded GitHub listing cannot include every record."""


class GitHub:
    """Bound gh output and lifetime; gh handles authentication and artifact redirects."""

    timeout = 60.0
    max_pages = 20

    def __init__(self, *, timeout: float = 60, max_pages: int = 20) -> None:
        self.timeout = timeout
        self.max_pages = max_pages

    def request(
        self, path: str, *, method: str = "GET", body: object = None, limit: int = MAX_JSON
    ) -> bytes:
        command = ["gh", "api", "--hostname", "github.com", "--method", method, path]
        with tempfile.TemporaryFile() as payload:
            if body is not None:
                payload.write(json.dumps(body).encode())
                payload.seek(0)
                command += ["--input", "-"]
            deadline = time.monotonic() + self.timeout
            with subprocess.Popen(
                command, stdin=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            ) as process:
                assert process.stdout is not None
                try:
                    data = bytearray()
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0 or not selector.select(remaining):
                                raise subprocess.TimeoutExpired(command, self.timeout)
                            chunk = os.read(
                                process.stdout.fileno(), min(65536, limit + 1 - len(data))
                            )
                            if not chunk:
                                break
                            data.extend(chunk)
                            if len(data) > limit:
                                raise ValueError("GitHub response exceeds size limit")
                    if process.wait(timeout=max(0, deadline - time.monotonic())):
                        raise ValueError("GitHub API request failed")
                    return bytes(data)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()

    def json(self, path: str, *, method: str = "GET", body: object = None) -> object:
        return cast(object, json.loads(self.request(path, method=method, body=body)))

    def pages(self, path: str, key: str | None = None) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, self.max_pages + 1):
            response = self.json(f"{path}{separator}per_page=100&page={page}")
            values = array(table(response)[key] if key else response)
            result.extend(table(value) for value in values)
            if len(values) < 100:
                return result
        raise PaginationLimit("GitHub pagination limit exceeded")
