"""Environment declares product DNS and separate authenticated operator origins."""

import json
import runpy
from pathlib import Path

import pulumi
import pytest
from host_mocks import HostMocks

ROOT = Path(__file__).resolve().parents[4]


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_environment_composes_service_owned_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = HostMocks()
    monkeypatch.setenv(
        "PULUMI_CONFIG",
        json.dumps(
            {
                "sanctuary:install": "true",
                "sanctuary:sourceDirectory": "/tmp/unused",
                "sanctuary:revision": "a" * 40,
                "sanctuary:runId": "1-1",
                "sanctuary:acmeEmail": "ops@example.com",
                "sanctuary:cloudflareDnsApiToken": "test-token",
                "sanctuary:registryConfig": "{}",
                "sanctuary:domain": "play.example.com",
                "sanctuary:cloudflareZoneId": "a" * 32,
                "sanctuary:cloudflareAccountId": "b" * 32,
                "sanctuary:operatorEmails": json.dumps(["ops@example.com"]),
                "sanctuary:grafanaHostname": "grafana.example.com",
                "sanctuary:temporalHostname": "temporal.example.com",
                "sanctuary:operatorTeamName": "operators",
                "sanctuary:sshCidrs": json.dumps(["203.0.113.1/32"]),
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


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_dns_names_preserve_existing_resource_identity() -> None:
    from cli_playground_infra.dns import DnsConfig, provision_dns

    mocks = HostMocks()
    provision_dns(DnsConfig(domain="play.example.com", cloudflare_zone_id="a" * 32), "203.0.113.1")
    await mocks.finish()
    for name, old in (
        ("cli-playground", "playground"),
        ("cli-playground-previews", "playground-previews"),
    ):
        registration = mocks.registrations["cloudflare:index/dnsRecord:DnsRecord", name]
        assert any(
            isinstance(alias, pulumi.Alias) and alias.name == old
            for alias in registration.opts.aliases or []
        )
