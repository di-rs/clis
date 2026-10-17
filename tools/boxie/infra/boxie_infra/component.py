"""Boxie-owned declarations receive explicit dependencies and never activate containers."""

import pulumi
from host_infra.contracts import IMAGE, REFERENCE, HostHandle, NetworkHandles
from host_infra.resources import Directory, Volume
from pydantic import BaseModel, ConfigDict, Field


class BoxieConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    image: str = Field(pattern="^" + IMAGE + "$")
    consumer_credential_ref: str = Field(pattern="^" + REFERENCE + "$")
    orchestrator_credential_ref: str = Field(pattern="^" + REFERENCE + "$")
    postgres_dsn_ref: str = Field(pattern="^" + REFERENCE + "$")
    temporal_address: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}:[1-9][0-9]{0,4}$")
    temporal_namespace: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    temporal_ca_ref: str = Field(pattern="^" + REFERENCE + "$")
    temporal_api_identity_ref: str = Field(pattern="^" + REFERENCE + "$")
    temporal_orchestrator_identity_ref: str = Field(pattern="^" + REFERENCE + "$")


class Boxie(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: BoxieConfig,
        *,
        host: HostHandle,
        networks: NetworkHandles,
        monitoring: bool = False,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("clis:boxie:Service", name, None, opts)
        self.endpoint = "http://boxie-api:8090"
        self.settings = {
            **config.model_dump(),
            "installation_id": host.installation_id,
            "host_id": host.host_id,
            "data_dir": host.data_root + "/boxie",
            "control_network": networks.control,
            "consumer_network": networks.consumer,
            "metrics_network": networks.metrics,
            "endpoint": self.endpoint,
        }
        if not monitoring:
            self.settings.pop("metrics_network")
        prefix = "clis-" + host.installation_id.replace("-", "")
        specs = [("postgres", 999, 999)]
        if monitoring:
            specs += [
                ("vector", 65532, 65532),
                ("logs", 65534, 65534),
                ("prometheus", 65534, 65534),
                ("grafana", 472, 0),
            ]
        self.volumes = tuple(
            Volume(f"{prefix}-{name}", f"volumes/{name}", uid, gid) for name, uid, gid in specs
        )
        self.directories = (
            Directory("boxie", 10001, 10001),
            Directory("approved-profiles", 0, 10001, 0o750),
        )
        self.register_outputs(self.settings)
