"""Thin single-host composition: declarations and references, never workload activation."""

from pathlib import Path
from typing import NamedTuple

import pulumi
from boxie_infra.access import (
    OperatorIngressConfig,
    OperatorIngressOutputs,
    OperatorOrigin,
    OperatorOrigins,
    provision_operator_access,
)
from boxie_infra.component import Boxie, BoxieConfig
from host_infra.contracts import IMAGE, REFERENCE, HostHandle, NetworkHandles
from host_infra.host import Host, HostConfig
from host_infra.resources import Network, ResourcePlan, Volume
from playground_infra.component import Playground, PlaygroundConfig
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    domain: str
    cloudflare_zone_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    image: str = Field(pattern="^" + IMAGE + "$")


class OperatorsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    zone_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    team_name: str
    grafana_hostname: str
    temporal_hostname: str
    allowed_identities: tuple[str, ...]

    def access(self) -> OperatorIngressConfig:
        return OperatorIngressConfig(
            self.team_name, self.grafana_hostname, self.temporal_hostname, self.allowed_identities
        )


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    host: HostConfig
    boxie_image: str = Field(pattern="^" + IMAGE + "$")
    consumer_credential_ref: str = Field(pattern="^" + REFERENCE + "$")
    playground: ProductConfig | None = None
    operators: OperatorsConfig | None = None
    monitoring: bool = False

    @model_validator(mode="after")
    def selected_operators_need_monitoring(self) -> EnvironmentConfig:
        if self.operators and not self.monitoring:
            raise ValueError("operator ingress requires monitoring selection")
        return self


class Environment(NamedTuple):
    host: Host
    boxie: Boxie
    playground: Playground | None
    operators: OperatorIngressOutputs | None
    resources: pulumi.Output[dict[str, object]]
    handles: dict[str, str]
    observability_settings: dict[str, object]


def compose(
    config: EnvironmentConfig, *, tunnel_secret: pulumi.Input[str] | None = None
) -> Environment:
    # Validate every component before registering the first cloud resource.
    if config.operators and tunnel_secret is None:
        raise ValueError("operator ingress requires its separately stored tunnel secret")
    access = config.operators.access() if config.operators else None
    boxie_config = BoxieConfig(
        image=config.boxie_image,
        consumer_credential_ref=config.consumer_credential_ref,
        orchestrator_credential_ref="BOXIE_ORCHESTRATOR_CREDENTIAL",
        postgres_dsn_ref="BOXIE_POSTGRES_DSN",
        temporal_address="temporal:7233",
        temporal_namespace="boxie",
        temporal_ca_ref="TEMPORAL_FRONTEND_CA",
        temporal_api_identity_ref="BOXIE_TEMPORAL_API_IDENTITY",
        temporal_orchestrator_identity_ref="BOXIE_TEMPORAL_ORCHESTRATOR_IDENTITY",
    )
    product_config = None
    if config.playground:
        product_config = PlaygroundConfig(
            **config.playground.model_dump(),
            boxie_endpoint="http://boxie-api:8090",
            consumer_credential_ref=config.consumer_credential_ref,
        )
    installation = str(config.host.installation_id)
    prefix = "clis-" + config.host.installation_id.hex
    host_handle = HostHandle(installation, "single-host", "/srv/boxie")
    networks = NetworkHandles.for_installation(installation)
    # The environment selects/wires owners. Pulumi never reconciles Compose containers.
    journal_policy = (
        Path(__file__).resolve().parents[3] / "tools/boxie/infra/observability/journald.conf"
    ).read_text()
    host = Host(
        "host",
        config.host.model_copy(update={"public_http": product_config is not None}),
        journal_policy=journal_policy,
    )
    boxie = Boxie(
        "boxie", boxie_config, host=host_handle, networks=networks, monitoring=config.monitoring
    )
    declarations = [
        Network(networks.control),
        Network(networks.consumer),
    ]
    if config.monitoring:
        declarations.extend(
            (
                Network(networks.metrics),
                Network(networks.grafana_origin),
                Network(networks.temporal_ui_origin),
            )
        )
    volumes = list(boxie.volumes)
    handles = {
        "BOXIE_CONTROL_NETWORK": networks.control,
        "BOXIE_CONSUMER_NETWORK": networks.consumer,
        "OBS_METRICS_NETWORK": networks.metrics,
        "OBS_GRAFANA_ORIGIN_NETWORK": networks.grafana_origin,
        "OBS_TEMPORAL_UI_ORIGIN_NETWORK": networks.temporal_ui_origin,
        "OPERATOR_GRAFANA_NETWORK": networks.grafana_origin,
        "OPERATOR_TEMPORAL_NETWORK": networks.temporal_ui_origin,
        "POSTGRES_VOLUME": prefix + "-postgres",
        "BOXIE_DATA_DIR": host_handle.data_root + "/boxie",
        "BOXIE_PROFILES_DIR": host_handle.data_root + "/approved-profiles",
        "OBS_VECTOR_VOLUME": prefix + "-vector",
        "OBS_LOGS_VOLUME": prefix + "-logs",
        "OBS_PROMETHEUS_VOLUME": prefix + "-prometheus",
        "OBS_GRAFANA_VOLUME": prefix + "-grafana",
    }
    product = None
    if product_config:
        declarations.append(Network(networks.public_proxy, False))
        volumes.extend(
            Volume(f"{prefix}-{suffix}", f"volumes/{suffix}", uid, gid)
            for suffix, uid, gid in (("playground-data", 10001, 10001), ("certificates", 0, 0))
        )
        handles.update(
            {
                "PLAYGROUND_CONSUMER_NETWORK": networks.consumer,
                "PLAYGROUND_PROXY_NETWORK": networks.public_proxy,
                "PLAYGROUND_DATA_VOLUME": prefix + "-playground-data",
                "PLAYGROUND_CERTIFICATES_VOLUME": prefix + "-certificates",
            }
        )
        product = Playground(
            "playground",
            product_config,
            host=host_handle,
            networks=networks,
            address=host.address,
            data_volume=handles["PLAYGROUND_DATA_VOLUME"],
            certificates_volume=handles["PLAYGROUND_CERTIFICATES_VOLUME"],
        )
    operators = None
    if config.operators and access and tunnel_secret is not None:
        operators = provision_operator_access(
            access,
            OperatorOrigins(
                OperatorOrigin("http://grafana:3000", networks.grafana_origin),
                OperatorOrigin("http://temporal-ui:8080", networks.temporal_ui_origin),
            ),
            account_id=config.operators.account_id,
            zone_id=config.operators.zone_id,
            tunnel_name=prefix,
            tunnel_secret=tunnel_secret,
            credentials_file="/run/secrets/tunnel.json",
        )
    resources: pulumi.Output[dict[str, object]] = host.storage_device.apply(
        lambda device: ResourcePlan(
            installation,
            device,
            host_handle.data_root,
            tuple(declarations),
            tuple(volumes),
            boxie.directories,
        ).to_dict()
    )
    observability_settings: dict[str, object] = (
        {
            "BOXIE_INSTALLATION_ID": installation,
            "BOXIE_HOST_ID": host_handle.host_id,
            "BOXIE_TEMPORAL_NAMESPACE": "boxie",
        }
        if config.monitoring
        else {}
    )
    return Environment(host, boxie, product, operators, resources, handles, observability_settings)
