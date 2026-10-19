"""Infrastructure contracts exercised through the real Pulumi resource graph."""

import json
import urllib.request
from pathlib import Path
from typing import Any, Never

import pulumi
import pytest
import yaml
from cli_playground_infra.host import HostConfig, provision
from pulumi.runtime.settings import get_root_resource, reset_options
from pulumi.runtime.stack import Stack

ZONE_ID = "0123456789abcdef0123456789abcdef"


def test_domain_requires_a_hostname() -> None:
    with pytest.raises(ValueError, match="domain"):
        HostConfig(domain="https://play.example.com", cloudflare_zone_id=ZONE_ID)


@pytest.mark.parametrize("zone_id", ["", "not-a-zone-id", "x" * 32])
def test_cloudflare_zone_requires_an_identifier(zone_id: str) -> None:
    with pytest.raises(ValueError, match="cloudflare_zone_id"):
        HostConfig(domain="play.example.com", cloudflare_zone_id=zone_id)


@pytest.mark.parametrize(
    "cidrs", [[], ["2001:db8::1/128"], ["203.0.113.4"], ["203.0.113.4/24"], ["invalid"]]
)
def test_ssh_requires_explicit_ipv4_networks(cidrs: list[str]) -> None:
    with pytest.raises(ValueError, match="sshCidrs"):
        HostConfig(
            domain="play.example.com",
            cloudflare_zone_id=ZONE_ID,
            ssh_cidrs=cidrs,
        )


@pytest.mark.parametrize(
    "key",
    ["-----BEGIN OPENSSH PRIVATE KEY-----", "command=bad ssh-ed25519 YWJj"],
)
def test_only_public_ssh_keys_are_accepted(key: str) -> None:
    with pytest.raises(ValueError, match="sshPublicKey"):
        HostConfig(
            domain="play.example.com",
            cloudflare_zone_id=ZONE_ID,
            ssh_public_key=key,
        )


class HostMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.inputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.registrations: dict[tuple[str, str], pulumi.ResourceTransformationArgs] = {}
        reset_options()
        pulumi.runtime.set_mocks(self, project="cli-playground", stack="test")
        pulumi.runtime.register_stack_transformation(self.observe)

    def observe(self, args: pulumi.ResourceTransformationArgs) -> None:
        self.registrations[args.type_, args.name] = args

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.inputs[args.typ, args.name] = args.inputs
        if args.typ == "tls:index/privateKey:PrivateKey":
            return args.name, {
                **args.inputs,
                "privateKeyOpenssh": f"{args.name}-private-openssh",
                "privateKeyPem": f"{args.name}-private-pem",
                "privateKeyPemPkcs8": f"{args.name}-private-pkcs8",
                "publicKeyOpenssh": f"ssh-ed25519 {args.name}-public\n",
                "publicKeyPem": f"{args.name}-public-pem",
                "publicKeyFingerprintMd5": f"{args.name}-md5",
                "publicKeyFingerprintSha256": f"{args.name}-sha256",
            }
        return "123", {**args.inputs, "ipAddress": "203.0.113.12", "ipv4Address": "203.0.113.12"}

    def call(self, args: pulumi.runtime.MockCallArgs) -> Never:
        raise AssertionError(f"Unexpected cloud lookup: {args.token}")

    async def finish(self) -> None:
        for registration in self.registrations.values():
            await registration.resource.urn.future()


@pulumi.runtime.test  # type: ignore[untyped-decorator]  # Pulumi's test runner is untyped.
async def test_resource_graph_limits_public_exposure_and_protects_host() -> None:
    mocks = HostMocks()
    server = provision(HostConfig(domain="play.example.com", cloudflare_zone_id=ZONE_ID))
    await mocks.finish()
    host = mocks.inputs["hcloud:index/server:Server", "sanctuary"]
    assert host["firewallIds"] == [123]
    assert host["deleteProtection"] is True
    assert host["rebuildProtection"] is True
    assert host["publicNets"] == [{"ipv4": 123, "ipv4Enabled": True, "ipv6Enabled": False}]
    firewall = mocks.inputs["hcloud:index/firewall:Firewall", "sanctuary"]
    tcp_rules = {
        rule["port"]: rule["sourceIps"] for rule in firewall["rules"] if rule["protocol"] == "tcp"
    }
    assert set(tcp_rules) == {"22", "80", "443"}
    assert tcp_rules["22"] == ["0.0.0.0/0"]
    assert ("hcloud:index/sshKey:SshKey", "admin") not in mocks.inputs
    ip = mocks.inputs["hcloud:index/primaryIp:PrimaryIp", "sanctuary-ipv4"]
    assert ip["autoDelete"] is False
    assert ip["deleteProtection"] is True
    assert mocks.registrations["hcloud:index/primaryIp:PrimaryIp", "sanctuary-ipv4"].opts.protect
    assert mocks.registrations["hcloud:index/server:Server", "sanctuary"].opts.protect

    records = {
        value["name"]: value
        for (kind, _), value in mocks.inputs.items()
        if kind == "cloudflare:index/dnsRecord:DnsRecord"
    }
    assert set(records) == {"play.example.com", "*.play.example.com"}
    for record in records.values():
        assert record["zoneId"] == ZONE_ID
        assert record["content"] == "203.0.113.12"
        assert record["type"] == "A"
        assert record["proxied"] is False

    root = get_root_resource()
    assert isinstance(root, Stack)
    assert set(root.outputs) == {"ipv4", "sshPrivateKey", "sshHostPublicKey", "publicUrl"}
    assert await pulumi.Output.from_input(root.outputs["ipv4"]).future() == "203.0.113.12"
    assert root.outputs["publicUrl"] == "https://play.example.com"
    assert await server.id.future() == "123"


@pulumi.runtime.test  # type: ignore[untyped-decorator]  # Pulumi's test runner is untyped.
async def test_optional_administrator_access_uses_first_boot_without_server_key_drift() -> None:
    mocks = HostMocks()
    server = provision(
        HostConfig(
            domain="play.example.com",
            cloudflare_zone_id=ZONE_ID,
            ssh_public_key="ssh-ed25519 YWJj",
            ssh_cidrs=["203.0.113.4/32"],
        )
    )
    await mocks.finish()
    firewall = mocks.inputs["hcloud:index/firewall:Firewall", "sanctuary"]
    ssh_rules = [rule for rule in firewall["rules"] if rule.get("port") == "22"]
    assert len(ssh_rules) == 1
    assert ssh_rules[0]["sourceIps"] == ["203.0.113.4/32"]
    assert ("hcloud:index/sshKey:SshKey", "admin") not in mocks.inputs
    assert len(mocks.inputs["hcloud:index/server:Server", "sanctuary"]["sshKeys"]) == 1
    user_data = await server.user_data.future()
    assert user_data is not None
    config = json.loads(user_data.removeprefix("#cloud-config\n"))
    assert any(
        entry["path"] == "/etc/sanctuary/admin-ssh-key" and entry["content"] == "ssh-ed25519 YWJj\n"
        for entry in config["write_files"]
    )


@pulumi.runtime.test  # type: ignore[untyped-decorator]  # Pulumi's test runner is untyped.
async def test_generated_ssh_identity_and_host_key_keep_private_material_secret() -> None:
    mocks = HostMocks()
    server = provision(HostConfig(domain="play.example.com", cloudflare_zone_id=ZONE_ID))
    await mocks.finish()
    keys = {
        name: inputs
        for (kind, name), inputs in mocks.inputs.items()
        if kind == "tls:index/privateKey:PrivateKey"
    }
    assert set(keys) == {"deployment-key", "host-key"}
    for name, inputs in keys.items():
        assert inputs["algorithm"] == "ED25519"
        options = mocks.registrations["tls:index/privateKey:PrivateKey", name].opts
        assert {"privateKeyOpenssh", "privateKeyPem", "privateKeyPemPkcs8"}.issubset(
            options.additional_secret_outputs or []
        )

    root = get_root_resource()
    assert isinstance(root, Stack)
    private_key = pulumi.Output.from_input(root.outputs["sshPrivateKey"])
    assert await private_key.is_secret()
    assert await private_key.future() == "deployment-key-private-openssh"
    host_public_key = pulumi.Output.from_input(root.outputs["sshHostPublicKey"])
    assert not await host_public_key.is_secret()
    assert await host_public_key.future() == "ssh-ed25519 host-key-public"
    deployment = mocks.inputs["hcloud:index/sshKey:SshKey", "deployment"]
    assert deployment["publicKey"] == "ssh-ed25519 deployment-key-public"
    assert await server.user_data.is_secret()
    user_data = await server.user_data.future()
    assert user_data is not None
    config = json.loads(user_data.removeprefix("#cloud-config\n"))
    assert config["ssh_keys"] == {
        "ed25519_private": "host-key-private-openssh",
        "ed25519_public": "ssh-ed25519 host-key-public",
    }
    assert config["ssh_pwauth"] is False
    sshd_config = next(
        entry["content"]
        for entry in config["write_files"]
        if entry["path"] == "/etc/ssh/sshd_config.d/00-sanctuary.conf"
    )
    assert sshd_config.splitlines() == [
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PermitRootLogin prohibit-password",
    ]
    assert config["runcmd"] == [["bash", "/usr/local/sbin/sanctuary-bootstrap"]]
    assert "deployment-key-private" not in user_data
    assert "HCLOUD_TOKEN" not in user_data
    assert "CLOUDFLARE_API_TOKEN" not in user_data


def test_production_routes_preview_hosts_with_dns_challenge_and_proxy_only_credentials() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "infra/compose.production.yml").read_text())
    gateway = compose["services"]["gateway"]
    assert gateway["environment"]["PLAYGROUND_PREVIEW_DOMAIN"] == "${PLAYGROUND_DOMAIN}"
    assert not any("CLOUDFLARE" in key or key.startswith("CF_") for key in gateway["environment"])
    assert "PLAYGROUND_BOXIE_URL" in gateway["environment"]
    assert "PLAYGROUND_SANDBOX_RUNTIME" not in gateway["environment"]
    proxy = compose["services"]["proxy"]
    assert (
        "--certificatesresolvers.preview.acme.dnschallenge.provider=cloudflare" in proxy["command"]
    )
    assert not any("httpchallenge" in argument for argument in proxy["command"])
    assert "env_file" not in proxy
    assert set(proxy["environment"]) == {"PLAYGROUND_DOMAIN", "CF_DNS_API_TOKEN"}
    assert "CLOUDFLARE_DNS_API_TOKEN" in proxy["environment"]["CF_DNS_API_TOKEN"]

    routes = yaml.safe_load((root / "infra/routes.yml").read_text())
    router = routes["http"]["routers"]["playground"]
    assert router["rule"] == (
        'Host(`{{ env "PLAYGROUND_DOMAIN" }}`) || '
        "HostRegexp(`^(pr-[1-9][0-9]*|[0-9a-f]{32})[.]"
        '{{ env "PLAYGROUND_DOMAIN" | replace "." "[.]" }}$`)'
    )
    assert router["entryPoints"] == ["websecure"]
    assert router["tls"] == {
        "certResolver": "preview",
        "domains": [
            {"main": '{{ env "PLAYGROUND_DOMAIN" }}', "sans": ['*.{{ env "PLAYGROUND_DOMAIN" }}']}
        ],
    }


def test_gateway_healthcheck_uses_public_url_host(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "infra/compose.production.yml").read_text())
    command = compose["services"]["gateway"]["healthcheck"]["test"]
    assert command[:3] == ["CMD", "/app/.venv/bin/python", "-c"]
    monkeypatch.setenv("PLAYGROUND_PUBLIC_URL", "https://play.example.com")
    monkeypatch.delenv("PLAYGROUND_PREVIEW_DOMAIN", raising=False)
    requests: list[urllib.request.Request] = []

    class Response:
        def close(self) -> None:
            pass

    def urlopen(request: urllib.request.Request, *, timeout: int) -> Response:
        assert timeout == 3
        requests.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    exec(command[3], {})
    assert len(requests) == 1
    assert requests[0].full_url == "http://127.0.0.1:8000/healthz"
    assert requests[0].get_header("Host") == "play.example.com"
