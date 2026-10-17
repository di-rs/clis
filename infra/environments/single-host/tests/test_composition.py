"""Environment wiring has its own tests; service-owned components stay independent."""

import importlib.util
from pathlib import Path
from typing import Any

import pulumi
import pytest
from host_infra.host import HostConfig

support_spec = importlib.util.spec_from_file_location(
    "infra_test_support", Path(__file__).parents[3] / "host/tests/test_host.py"
)
assert support_spec is not None and support_spec.loader is not None
support = importlib.util.module_from_spec(support_spec)
support_spec.loader.exec_module(support)
HostMocks = support.HostMocks
INSTALLATION = support.INSTALLATION
PUBLIC_KEY = support.PUBLIC_KEY

ROOT = Path(__file__).parents[1]
IMAGE = "ghcr.io/example/boxie@sha256:" + "a" * 64


def api() -> Any:
    entry = ROOT / "environment.py"
    assert entry.is_file(), "thin environment composition is missing"
    spec = importlib.util.spec_from_file_location("single_host_environment", entry)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_configuration_is_optional_and_unknown_inputs_are_rejected() -> None:
    a = api()
    generic = a.EnvironmentConfig(
        host=HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
        boxie_image=IMAGE,
        consumer_credential_ref="BOXIE_CONSUMER_CREDENTIAL",
    )
    assert generic.playground is None and generic.operators is None
    with pytest.raises(ValueError):
        a.EnvironmentConfig(
            host=generic.host,
            boxie_image="latest",
            consumer_credential_ref="BOXIE_CONSUMER_CREDENTIAL",
        )


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_generic_environment_has_no_product_dns_and_only_nonsecret_references() -> None:
    a = api()
    mocks = HostMocks()
    environment = a.compose(
        a.EnvironmentConfig(
            host=HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
            boxie_image=IMAGE,
            consumer_credential_ref="BOXIE_CONSUMER_CREDENTIAL",
        )
    )
    profile = await environment.host.connection_profile.future()
    plan = await environment.resources.future()
    for item in mocks.resources:
        await item.resource.urn.future()
    assert profile["host_public_key"] == PUBLIC_KEY
    assert environment.playground is None
    assert environment.boxie.endpoint == "http://boxie-api:8090"
    assert environment.boxie.settings["consumer_credential_ref"] == "BOXIE_CONSUMER_CREDENTIAL"
    assert environment.boxie.settings["temporal_address"] == "temporal:7233"
    assert environment.boxie.settings["postgres_dsn_ref"] == "BOXIE_POSTGRES_DSN"
    assert {n["name"].rsplit("-", 1)[-1] for n in plan["networks"]} >= {
        "control",
        "consumer",
    }
    assert all(n["internal"] for n in plan["networks"])
    assert all(v["directory"].startswith("volumes/") for v in plan["volumes"])
    assert not await environment.resources.is_secret()
    assert not any("cloudflare" in kind for kind, _ in mocks.inputs)


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_product_receives_supplied_private_endpoint_without_owning_boxie() -> None:
    mocks = HostMocks()
    from host_infra.contracts import HostHandle, NetworkHandles
    from playground_infra.component import Playground, PlaygroundConfig

    playground = Playground(
        "playground",
        PlaygroundConfig(
            domain="play.example.com",
            cloudflare_zone_id="b" * 32,
            image="ghcr.io/example/playground@sha256:" + "c" * 64,
            boxie_endpoint="http://supplied-api:8090",
            consumer_credential_ref="PRODUCT_CONSUMER_KEY",
        ),
        host=HostHandle(INSTALLATION, "host", "/srv/boxie"),
        networks=NetworkHandles.for_installation(INSTALLATION),
        address=pulumi.Output.from_input("192.0.2.10"),
        data_volume="supplied-data",
        certificates_volume="supplied-certificates",
    )
    for item in mocks.resources:
        await item.resource.urn.future()
    assert playground.settings["boxie_endpoint"] == "http://supplied-api:8090"
    dns = [v for (kind, _), v in mocks.inputs.items() if "dnsRecord" in kind]
    assert {v["name"] for v in dns} == {"play.example.com", "*.play.example.com"}
    assert not any("Boxie" in item.type_ for item in mocks.resources)


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_selected_product_adds_its_dns_public_bridge_and_storage() -> None:
    a = api()
    mocks = HostMocks()
    result = a.compose(
        a.EnvironmentConfig(
            host=HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
            boxie_image=IMAGE,
            consumer_credential_ref="BOXIE_CONSUMER_CREDENTIAL",
            playground=a.ProductConfig(
                domain="play.example.com",
                cloudflare_zone_id="b" * 32,
                image="ghcr.io/example/playground@sha256:" + "c" * 64,
            ),
        )
    )
    plan = await result.resources.future()
    for item in mocks.resources:
        await item.resource.urn.future()
    assert result.playground.settings["boxie_endpoint"] == result.boxie.endpoint
    assert len([n for n in plan["networks"] if n["internal"] is False]) == 1
    product_volume = next(v for v in plan["volumes"] if v["name"].endswith("-playground-data"))
    assert product_volume["uid"] == product_volume["gid"] == 10001
    rules = next(v for (t, _), v in mocks.inputs.items() if t == "hcloud:index/firewall:Firewall")[
        "rules"
    ]
    assert {r["port"] for r in rules if r["protocol"] == "tcp"} == {"22", "80", "443"}


def test_invalid_optional_inputs_fail_before_cloud_registration() -> None:
    a = api()
    mocks = HostMocks()
    settings = {
        "host": HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
        "boxie_image": IMAGE,
        "consumer_credential_ref": "BOXIE_CONSUMER_CREDENTIAL",
    }
    with pytest.raises(ValueError):
        a.compose(
            a.EnvironmentConfig(
                **settings,
                playground=a.ProductConfig(
                    domain="https://bad.example.com",
                    cloudflare_zone_id="b" * 32,
                    image=IMAGE,
                ),
            )
        )
    with pytest.raises(ValueError):
        a.compose(
            a.EnvironmentConfig(
                **settings,
                operators=a.OperatorsConfig(
                    account_id="a" * 32,
                    zone_id="b" * 32,
                    team_name="operators",
                    grafana_hostname="grafana.example.com",
                    temporal_hostname="temporal.example.com",
                    allowed_identities=("operator@example.com",),
                ),
            )
        )
    assert not mocks.resources


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_environment_supplies_the_observability_owned_journal_policy() -> None:
    a = api()
    HostMocks()
    result = a.compose(
        a.EnvironmentConfig(
            host=HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
            boxie_image=IMAGE,
            consumer_credential_ref="BOXIE_CONSUMER_CREDENTIAL",
        )
    )
    import json

    raw = await result.host.server.user_data.future()
    assert raw is not None
    files = {
        item["path"]: item["content"]
        for item in json.loads(raw.removeprefix("#cloud-config\n"))["write_files"]
    }
    policy = Path("tools/boxie/infra/observability/journald.conf").read_text()
    assert files.get("/etc/systemd/journald.conf.d/60-clis.conf") == policy


def test_native_monitoring_covers_both_backing_mounts() -> None:
    import yaml

    assets = Path("tools/boxie/infra/observability")
    compose = yaml.safe_load((assets / "compose.yml").read_text())
    assert (
        "--collector.filesystem.mount-points-include=^(/|/srv/boxie)$"
        in compose["services"]["node-exporter"]["command"]
    )
    rules = yaml.safe_load((assets / "rules/boxie.yml").read_text())["groups"][0]["rules"]
    disk = next(rule for rule in rules if rule["alert"] == "HostDiskSpaceLow")
    assert "/srv/boxie" in disk["expr"]


def test_owners_are_native_and_postgres_has_one_definition() -> None:
    root = ROOT.parents[2]
    assert not (root / "infra/platform").exists()
    assert not (root / "infra/observability").exists()
    assert not (root / "infra/ingress").exists()
    assert (root / "tools/boxie/infra/postgres/compose.production.yml").is_file()


def test_monitoring_requires_explicit_selection() -> None:
    a = api()
    config = a.EnvironmentConfig(
        host=HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
        boxie_image=IMAGE,
        consumer_credential_ref="BOXIE_CONSUMER_CREDENTIAL",
    )
    assert config.monitoring is False
