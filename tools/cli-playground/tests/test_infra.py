"""Infrastructure input boundaries; no cloud credentials or resources needed."""

from typing import Any, Never

import pulumi
import pytest
from infra.host import HostConfig, provision


def test_domain_requires_a_hostname() -> None:
    with pytest.raises(ValueError, match="domain"):
        HostConfig(
            domain="https://play.example.com",
            ssh_public_key="ssh-ed25519 YWJj",
            admin_cidrs=["203.0.113.4/32"],
        )


@pytest.mark.parametrize("cidrs", [["0.0.0.0/0"], ["2001:db8::1/128"]])
def test_ssh_requires_explicit_non_global_cidrs(cidrs: list[str]) -> None:
    with pytest.raises(ValueError, match="adminCidrs"):
        HostConfig(domain="play.example.com", ssh_public_key="ssh-ed25519 YWJj", admin_cidrs=cidrs)


@pytest.mark.parametrize(
    "key",
    [
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "command=bad ssh-ed25519 YWJj",
    ],
)
def test_only_public_ssh_keys_are_accepted(key: str) -> None:
    with pytest.raises(ValueError, match="sshPublicKey"):
        HostConfig(domain="play.example.com", ssh_public_key=key, admin_cidrs=["203.0.113.4/32"])


class HostMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.resources[args.typ] = args.inputs
        return "123", {**args.inputs, "ipAddress": "203.0.113.12"}

    def call(self, args: pulumi.runtime.MockCallArgs) -> Never:
        raise AssertionError(f"Unexpected cloud lookup: {args.token}")


@pulumi.runtime.test  # type: ignore[untyped-decorator]  # Pulumi's test runner is untyped.
def test_resource_graph_limits_public_exposure_and_protects_host() -> pulumi.Output[None]:
    mocks = HostMocks()
    pulumi.runtime.set_mocks(mocks, project="cli-playground", stack="test")
    server = provision(
        HostConfig(
            domain="play.example.com",
            ssh_public_key="ssh-ed25519 YWJj",
            admin_cidrs=["203.0.113.4/32"],
        )
    )

    def check(_: str) -> None:
        host = mocks.resources["hcloud:index/server:Server"]
        assert host["firewallIds"] == [123]
        assert host["deleteProtection"] is True
        assert host["rebuildProtection"] is True
        assert host["publicNets"] == [{"ipv4": 123, "ipv4Enabled": True, "ipv6Enabled": False}]
        firewall = mocks.resources["hcloud:index/firewall:Firewall"]
        tcp_rules = {
            rule["port"]: rule["sourceIps"]
            for rule in firewall["rules"]
            if rule["protocol"] == "tcp"
        }
        assert set(tcp_rules) == {"22", "80", "443"}
        assert tcp_rules["22"] == ["203.0.113.4/32"]
        ip = mocks.resources["hcloud:index/primaryIp:PrimaryIp"]
        assert ip["autoDelete"] is False
        assert ip["deleteProtection"] is True

    return server.id.apply(check)
