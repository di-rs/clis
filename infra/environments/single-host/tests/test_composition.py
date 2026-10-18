"""Environment declares product DNS and separate authenticated operator origins."""

import json
import runpy
from pathlib import Path

import pulumi
import pytest
from cli_check.workspace import find_root
from host_infra.testing import HostMocks

ROOT = find_root(Path(__file__).resolve().parent)


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_environment_composes_service_owned_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = HostMocks()
    monkeypatch.setenv(
        "PULUMI_CONFIG",
        json.dumps(
            {
                "clis:install": "true",
                "clis:sourceDirectory": "/tmp/unused",
                "clis:revision": "a" * 40,
                "clis:runId": "1-1",
                "clis:acmeEmail": "ops@example.com",
                "clis:cloudflareDnsApiToken": "test-token",
                "clis:registryConfig": "{}",
                "clis:domain": "play.example.com",
                "clis:cloudflareZoneId": "a" * 32,
                "clis:cloudflareAccountId": "b" * 32,
                "clis:operatorEmails": json.dumps(["ops@example.com"]),
                "clis:grafanaHostname": "grafana.example.com",
                "clis:temporalHostname": "temporal.example.com",
                "clis:operatorTeamName": "operators",
                "clis:sshCidrs": json.dumps(["203.0.113.1/32"]),
            }
        ),
    )
    runpy.run_path(str(ROOT / "infra/environments/single-host/__main__.py"))
    await mocks.finish()
    records = [
        v for (kind, _), v in mocks.inputs.items() if kind == "cloudflare:index/dnsRecord:DnsRecord"
    ]
    assert {v["name"] for v in records} == {
        "play.example.com",
        "*.play.example.com",
        "grafana.example.com",
        "temporal.example.com",
    }
    apps = [
        v
        for (kind, _), v in mocks.inputs.items()
        if kind == "cloudflare:index/zeroTrustAccessApplication:ZeroTrustAccessApplication"
    ]
    assert {v["domain"] for v in apps} == {"grafana.example.com", "temporal.example.com"}
    for app in apps:
        assert app["policies"][0]["decision"] == "allow"
        assert app["policies"][0]["includes"] == [{"email": {"email": "ops@example.com"}}]

    assert not any(kind.startswith("command:") for kind, _ in mocks.inputs)
