"""Probe explicit authorized existing Access routes; never create cloud resources."""

import argparse
import ipaddress
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class AccessSmokeConfig:
    url: str
    approved_user_cookie: Path
    wrong_audience_cookie: Path
    direct_origins: tuple[str, ...]
    expected_body: str

    def __post_init__(self) -> None:
        origin = urlsplit(self.url)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.fragment
        ):
            raise ValueError("explicit HTTPS protected route required")
        if not self.expected_body or not self.direct_origins:
            raise ValueError("application response marker and direct origin targets required")
        for value in self.direct_origins:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("explicit origin IP endpoint required")
            ipaddress.ip_address(parsed.hostname)


def run_smoke(
    config: AccessSmokeConfig | None,
    timeout_seconds: float = 30,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    if config is None:
        return {
            "status": "outstanding",
            "activation_ready": False,
            "reason": "authorized existing Access environment not configured",
        }
    if timeout_seconds <= 0:
        raise ValueError("positive deadline required")
    end = time.monotonic() + timeout_seconds
    statuses: dict[str, int | str] = {}
    with httpx.Client(follow_redirects=False, trust_env=False, transport=transport) as client:

        def request(url: str, cookie: str = "") -> httpx.Response:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Access smoke deadline")
            return client.get(
                url,
                headers={"Cookie": "CF_Authorization=" + cookie} if cookie else {},
                timeout=min(3, remaining),
            )

        for name, cookie in (
            ("unauthenticated", ""),
            ("forged", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJmb3JnZWQifQ."),
            ("wrong_audience", config.wrong_audience_cookie.read_text().strip()),
        ):
            response = request(config.url, cookie)
            denied = response.status_code in (401, 403) or (
                response.status_code in (302, 303, 307)
                and (urlsplit(response.headers.get("location", "")).hostname or "").endswith(
                    ".cloudflareaccess.com"
                )
            )
            if not denied:
                raise RuntimeError(name + " Access denial not observed")
            statuses[name] = response.status_code
        response = request(config.url, config.approved_user_cookie.read_text().strip())
        if response.status_code != 200 or config.expected_body not in response.text:
            raise RuntimeError("approved user did not reach expected application")
        statuses["approved_user"] = response.status_code
        for index, origin in enumerate(config.direct_origins):
            try:
                request(origin)
            except httpx.ConnectError, httpx.ConnectTimeout:
                statuses[f"direct_origin_{index}"] = "connection prevented"
            else:
                raise RuntimeError("direct origin reachable, including application login responses")
    return {
        "status": "passed",
        "activation_ready": True,
        "checks": statuses,
        "scope": "only the explicitly supplied route, user credentials and origin targets",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    config = None
    if args.config:
        data = json.loads(args.config.read_text())
        data["approved_user_cookie"] = Path(data["approved_user_cookie"])
        data["wrong_audience_cookie"] = Path(data["wrong_audience_cookie"])
        data["direct_origins"] = tuple(data["direct_origins"])
        config = AccessSmokeConfig(**data)
    try:
        report = run_smoke(config)
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(error).__name__, "activation_ready": False}
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(report))
    raise SystemExit(0 if report["status"] == "passed" else 2)


if __name__ == "__main__":
    main()
