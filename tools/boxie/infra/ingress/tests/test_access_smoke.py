"""Access response classification only; these mocks are not live authorization proof."""

import importlib
from pathlib import Path

import httpx
import pytest


def test_missing_environment_is_outstanding_without_network() -> None:
    smoke = importlib.import_module("ingress_infra.smoke")
    report = smoke.run_smoke(None)
    assert report["status"] == "outstanding"
    assert report["activation_ready"] is False


def test_unauthenticated_success_cannot_pass_access_probe(tmp_path: Path) -> None:
    smoke = importlib.import_module("ingress_infra.smoke")
    cookie = tmp_path / "cookie"
    cookie.write_text("synthetic-token")
    config = smoke.AccessSmokeConfig(
        "https://grafana.example.com/api/health",
        cookie,
        cookie,
        ("https://192.0.2.1/",),
        "database",
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="database"))
    with pytest.raises(RuntimeError, match="unauthenticated"):
        smoke.run_smoke(config, transport=transport)


def test_direct_origin_http_response_fails_even_with_application_login(tmp_path: Path) -> None:
    smoke = importlib.import_module("ingress_infra.smoke")
    valid, wrong = tmp_path / "valid", tmp_path / "wrong"
    valid.write_text("valid-private")
    wrong.write_text("wrong-private")
    config = smoke.AccessSmokeConfig(
        "https://grafana.example.com/api/health", valid, wrong, ("https://192.0.2.1/",), "database"
    )

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.host == "192.0.2.1":
            return httpx.Response(401)
        if "valid-private" in request.headers.get("cookie", ""):
            return httpx.Response(200, text="database")
        return httpx.Response(403)

    with pytest.raises(RuntimeError, match="direct origin"):
        smoke.run_smoke(config, transport=httpx.MockTransport(reply))


def test_unconfigured_module_is_an_explicit_nonpassing_exit() -> None:
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "ingress_infra.smoke"],
        capture_output=True,
        timeout=5,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    assert result.returncode == 2
    assert result.stderr == b""
    assert result.stdout == (
        b'{"status": "outstanding", "activation_ready": false, '
        b'"reason": "authorized existing Access environment not configured"}\n'
    )


@pytest.mark.parametrize("failure,blocked", [("certificate", False), ("refused", True)])
def test_direct_origin_distinguishes_tls_failure_from_refused_tcp(
    tmp_path: Path, failure: str, blocked: bool
) -> None:
    import ssl

    smoke = importlib.import_module("ingress_infra.smoke")
    valid, wrong = tmp_path / "valid", tmp_path / "wrong"
    valid.write_text("valid-private")
    wrong.write_text("wrong-private")
    config = smoke.AccessSmokeConfig(
        "https://grafana.example.com/api/health", valid, wrong, ("https://192.0.2.1/",), "database"
    )

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.host == "192.0.2.1":
            cause = (
                ssl.SSLCertVerificationError("IP address mismatch")
                if failure == "certificate"
                else ConnectionRefusedError(61, "connection refused")
            )
            raise httpx.ConnectError("connection failed") from cause
        if "valid-private" in request.headers.get("cookie", ""):
            return httpx.Response(200, text="database")
        return httpx.Response(403)

    if blocked:
        assert smoke.run_smoke(config, transport=httpx.MockTransport(reply))["activation_ready"]
    else:
        with pytest.raises(RuntimeError, match="direct origin"):
            smoke.run_smoke(config, transport=httpx.MockTransport(reply))
