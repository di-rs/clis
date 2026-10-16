"""Quiet, bounded production health probe; no public protocol changes."""

import json
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from playground.config import Settings, read_boxie_credential


class HealthError(Exception):
    """Readiness failed without exposing credentials, private URLs, or response data."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def expired(_signum: int, _frame: object) -> None:
    raise HealthError("readiness deadline exceeded")


@contextmanager
def deadline(seconds: float) -> Iterator[None]:
    started = time.monotonic()
    old_handler = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    budget = min(seconds, old_timer[0]) if old_timer[0] else seconds
    signal.setitimer(signal.ITIMER_REAL, budget)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0]:
            remaining = old_timer[0] - (time.monotonic() - started)
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, remaining), old_timer[1])


def fetch(url: str, headers: dict[str, str], timeout: float) -> object:
    try:
        # No environment proxy and no redirect can receive the consumer credential.
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with (
            deadline(timeout),
            opener.open(Request(url, headers=headers), timeout=timeout) as response,
        ):
            if response.status != 200:
                raise HealthError("dependency not ready")
            data = response.read(4097)
            if len(data) > 4096:
                raise HealthError("invalid readiness response")
            return json.loads(data)
    except OSError, URLError, ValueError:
        raise HealthError("dependency readiness failed") from None


def check(settings: Settings, *, local_origin: str = "http://127.0.0.1:8000") -> None:
    with deadline(8):
        credential = read_boxie_credential(settings.boxie_credential_file)
        local = fetch(local_origin + "/healthz", {"Host": urlsplit(settings.public_url).netloc}, 2)
        if not isinstance(local, dict) or local.get("status") != "ok":
            raise HealthError("gateway not ready")
        upstream = fetch(
            settings.boxie_url + "/readyz", {"Authorization": "Bearer " + credential}, 5
        )
        dependencies = upstream.get("dependencies") if isinstance(upstream, dict) else None
        if (
            not isinstance(dependencies, dict)
            or not dependencies
            or not all(
                isinstance(key, str) and key and value is True
                for key, value in dependencies.items()
            )
        ):
            raise HealthError("dependency not ready")


def main() -> int:
    try:
        check(Settings())
        return 0
    except Exception:
        # Docker stores healthcheck output. Keep every failure, including settings,
        # filesystem and parsing failures, free of secret values and traceback text.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
