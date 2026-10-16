"""Focused fixture regressions; Docker transport is not a native runsc proof."""

import socket
import time
from pathlib import Path
from string import Template
from uuid import uuid4

import httpx
import pytest
import yaml
from run_integration import ROOT, ProductionStack, command


@pytest.mark.integration
@pytest.mark.parametrize("service, target", [("api", 8090), ("temporal", 7233)])
def test_native_published_endpoint_survives_process_restart(
    tmp_path: Path, service: str, target: int
) -> None:
    # Exercise the actual native fixture's publish specification against Docker.
    config = yaml.safe_load(
        (ROOT / "tools/boxie/infra/compose.test.yml").read_text().replace("!override", "")
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    publish = Template(config["services"]["api"]["ports"][0]).substitute(
        BOXIE_TEST_API_PORT=str(port)
    )
    if service == "temporal":
        ProductionStack(tmp_path).write_overrides()
        override = yaml.safe_load((tmp_path / "temporal-override.yml").read_text())
        publish = override["services"]["temporal"]["ports"][0]
    name = "boxie-port-" + uuid4().hex
    script = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503)
        self.end_headers()
    def log_message(self, *args): pass
HTTPServer(('0.0.0.0', int(sys.argv[1])), Handler).serve_forever()
"""
    try:
        command(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--publish",
                publish,
                "python:3.15.0-slim",
                "python",
                "-c",
                script,
                str(target),
            ]
        )
        endpoint = command(["docker", "port", name, f"{target}/tcp"])
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(60):
                try:
                    assert client.get("http://" + endpoint + "/readyz").status_code == 503
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                pytest.fail("fixture HTTP dependency status never became reachable")
            command(["docker", "kill", name])
            command(["docker", "start", name])
            restarted = command(["docker", "port", name, f"{target}/tcp"])
            print(f"published {service} endpoint: before={endpoint}, after={restarted}")
            assert restarted == endpoint, "restart invalidated existing host clients"
            for _ in range(60):
                try:
                    assert client.get("http://" + endpoint + "/readyz").status_code == 503
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                pytest.fail("restarted endpoint unreachable; this is not a dependency 503")
    finally:
        command(["docker", "rm", "--force", "--volumes", name])
