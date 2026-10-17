"""Declaration tests do not establish live Access authorization."""

import base64
import dataclasses
import importlib.util
import json
from pathlib import Path
from typing import Any, Never

import pulumi
import pytest
import yaml
from pulumi.runtime.settings import reset_options

OWNER = Path(__file__).parents[1]
TUNNEL = "12345678-1234-1234-1234-123456789abc"


def api() -> Any:
    assert importlib.util.find_spec("ingress_infra"), "declared ingress owner missing"
    from ingress_infra import access

    return access


def config() -> Any:
    return api().OperatorIngressConfig(
        "operators", "grafana.example.com", "temporal.example.com", ("operator@example.com",)
    )


def origins() -> Any:
    a = api()
    return a.OperatorOrigins(
        a.OperatorOrigin("http://grafana:3000", "grafana-front"),
        a.OperatorOrigin("http://temporal-ui:8080", "temporal-front"),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"team_name": ""},
        {"team_name": "bad.cloud"},
        {"grafana_hostname": "*.example.com"},
        {"grafana_hostname": "https://grafana.example.com"},
        {"temporal_hostname": "grafana.example.com"},
        {"allowed_identities": ()},
        {"allowed_identities": ("*@example.com",)},
        {"allowed_identities": ("everyone",)},
    ],
)
def test_rejects_broad_or_missing_identity_boundary(change: dict[str, Any]) -> None:
    c = config()
    with pytest.raises(ValueError):
        dataclasses.replace(c, **change)


@pytest.mark.parametrize(
    "url",
    [
        "http://boxie-api:9101",
        "http://grafana:3000/path",
        "http://u:p@grafana:3000",
        "http://127.0.0.1:3000",
        "https://public.example.com",
    ],
)
def test_rejects_unexpected_origins(url: str) -> None:
    a = api()
    with pytest.raises(ValueError):
        a.OperatorOrigins(
            a.OperatorOrigin(url, "front-a"), a.OperatorOrigin("http://temporal-ui:8080", "front-b")
        )


def test_config_requires_distinct_audiences_and_final_404() -> None:
    a = api()
    for audiences in [("", "temporal-aud"), ("same", "same")]:
        with pytest.raises(ValueError):
            a.render_cloudflared(
                config(), origins(), TUNNEL, *audiences, "/run/secrets/tunnel.json"
            )
    value = a.render_cloudflared(
        config(), origins(), TUNNEL, "grafana-aud", "temporal-aud", "/run/secrets/tunnel.json"
    )
    parsed = yaml.safe_load(value)
    assert parsed["ingress"][-1] == {"service": "http_status:404"}
    assert len(parsed["ingress"]) == 3
    for rule, audience in zip(parsed["ingress"][:2], ["grafana-aud", "temporal-aud"], strict=True):
        assert rule["originRequest"]["access"] == {
            "required": True,
            "teamName": "operators",
            "audTag": [audience],
        }


class AccessMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.inputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.resources: list[pulumi.Resource] = []
        reset_options()
        pulumi.runtime.set_mocks(self, project="operators", stack="test")
        pulumi.runtime.register_stack_transformation(self.observe)

    def observe(self, args: pulumi.ResourceTransformationArgs) -> None:
        self.resources.append(args.resource)

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.inputs[args.typ, args.name] = args.inputs
        return (TUNNEL if "Tunnel" in args.typ else args.name), {
            **args.inputs,
            "aud": args.name + "-aud",
        }

    def call(self, args: pulumi.runtime.MockCallArgs) -> Never:
        raise AssertionError(f"unexpected lookup {args.token}")


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_exact_access_resource_graph_and_secret_outputs() -> None:
    a = api()
    mocks = AccessMocks()
    secret = base64.b64encode(b"fixture-only-stable-secret-32bytes").decode()
    result = a.provision_operator_access(
        config(),
        origins(),
        account_id="a" * 32,
        zone_id="b" * 32,
        tunnel_name="operator-fixture",
        tunnel_secret=pulumi.Output.secret(secret),
        credentials_file="/run/secrets/tunnel.json",
    )
    parsed = yaml.safe_load(await result.cloudflared_config.future())
    for resource in mocks.resources:
        await resource.urn.future()
    apps = [v for (t, _), v in mocks.inputs.items() if "AccessApplication" in t]
    assert len(apps) == 2
    assert {v["domain"] for v in apps} == {"grafana.example.com", "temporal.example.com"}
    for app in apps:
        assert app["type"] == "self_hosted"
        assert app["destinations"] == [{"type": "public", "uri": app["domain"]}]
        assert app["optionsPreflightBypass"] is False
        assert app["policies"] == [
            {
                "name": "Approved operators",
                "decision": "allow",
                "precedence": 1,
                "includes": [{"email": {"email": "operator@example.com"}}],
            }
        ]
    dns = [v for (t, _), v in mocks.inputs.items() if "dnsRecord" in t]
    assert len(dns) == 2
    assert all(
        v["content"] == TUNNEL + ".cfargotunnel.com" and v["proxied"] and v["type"] == "CNAME"
        for v in dns
    )
    assert await result.grafana_audience.future() == "operator-grafana-aud"
    assert await result.temporal_audience.future() == "operator-temporal-aud"
    assert await result.tunnel_credentials_json.is_secret()
    assert not await result.cloudflared_config.is_secret()
    assert secret not in json.dumps(parsed)
    assert json.loads(await result.tunnel_credentials_json.future()) == {
        "AccountTag": "a" * 32,
        "TunnelID": TUNNEL,
        "TunnelSecret": secret,
    }
    assert len(mocks.inputs) == 5


def test_only_dashboard_networks_and_no_host_ports() -> None:
    assert (OWNER / "compose.yml").is_file(), "ingress manifest missing"
    compose = yaml.safe_load((OWNER / "compose.yml").read_text())
    service = compose["services"]["cloudflared"]
    versions = json.loads(Path("tools/boxie/infra/observability/versions.json").read_text())
    assert service["image"] == versions["images"]["cloudflared"]["image"]
    assert set(service["networks"]) == {"grafana-origin", "temporal-origin", "egress"}
    assert not service.get("ports") and "docker.sock" not in json.dumps(compose)
    assert service.get("network_mode") != "host"
    assert all(v.endswith(":ro") for v in service["volumes"])
    for path in [
        "tools/boxie/infra/observability/compose.yml",
        "tools/boxie/infra/temporal/compose.yml",
    ]:
        for item in yaml.safe_load(Path(path).read_text())["services"].values():
            assert not item.get("ports")


@pytest.mark.parametrize(
    "change",
    [
        {"account_id": ""},
        {"zone_id": "bad"},
        {"tunnel_name": "bad name"},
        {"credentials_file": "/tmp/token"},
        {"tunnel_secret": "not-base64"},
    ],
)
def test_invalid_ordinary_provision_inputs_register_nothing(change: dict[str, Any]) -> None:
    a = api()
    mocks = AccessMocks()
    values = {
        "account_id": "a" * 32,
        "zone_id": "b" * 32,
        "tunnel_name": "operators",
        "credentials_file": "/run/secrets/tunnel.json",
        "tunnel_secret": base64.b64encode(b"x" * 32).decode(),
    }
    values.update(change)
    with pytest.raises(ValueError):
        a.provision_operator_access(config(), origins(), **values)
    assert not mocks.resources


def test_origins_cannot_share_or_swap_network_boundary() -> None:
    a = api()
    with pytest.raises(ValueError):
        a.OperatorOrigins(
            a.OperatorOrigin("http://grafana:3000", "shared"),
            a.OperatorOrigin("http://temporal-ui:8080", "shared"),
        )
    with pytest.raises(ValueError):
        a.OperatorOrigins(
            a.OperatorOrigin("http://temporal-ui:8080", "a"),
            a.OperatorOrigin("http://grafana:3000", "b"),
        )
