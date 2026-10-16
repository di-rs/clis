"""Product-owned DNS and deployment declarations over supplied private service handles."""

import re

import pulumi
import pulumi_cloudflare as cloudflare
from platform_infra.contracts import IMAGE, REFERENCE, HostHandle, NetworkHandles, name
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlaygroundConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    domain: str = Field(max_length=220)
    cloudflare_zone_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    image: str = Field(pattern="^" + IMAGE + "$")
    boxie_endpoint: str = Field(pattern=r"^http://[a-z][a-z0-9-]{0,62}:[1-9][0-9]{0,4}$")
    consumer_credential_ref: str = Field(pattern="^" + REFERENCE + "$")
    deploy_token_ref: str = Field(default="PLAYGROUND_DEPLOY_TOKEN", pattern="^" + REFERENCE + "$")
    dns_token_ref: str = Field(default="CLOUDFLARE_DNS_API_TOKEN", pattern="^" + REFERENCE + "$")

    @field_validator("domain")
    @classmethod
    def dns_hostname(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
            raise ValueError("exact lowercase product hostname required")
        return value


class Playground(pulumi.ComponentResource):
    def __init__(
        self,
        resource_name: str,
        config: PlaygroundConfig,
        *,
        host: HostHandle,
        networks: NetworkHandles,
        address: pulumi.Input[str],
        data_volume: str,
        certificates_volume: str,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        name(data_volume)
        name(certificates_volume)
        if data_volume == certificates_volume:
            raise ValueError("product data and certificate storage must be independent")
        super().__init__("clis:playground:Service", resource_name, None, opts)
        for suffix, hostname in (("product", config.domain), ("previews", "*." + config.domain)):
            cloudflare.DnsRecord(
                f"{resource_name}-{suffix}",
                zone_id=config.cloudflare_zone_id,
                name=hostname,
                type="A",
                content=address,
                ttl=300,
                proxied=False,
                opts=pulumi.ResourceOptions(parent=self),
            )
        self.settings = {
            **config.model_dump(),
            "installation_id": host.installation_id,
            "consumer_network": networks.consumer,
            "metrics_network": networks.metrics,
            "proxy_network": networks.public_proxy,
            "data_volume": data_volume,
            "certificates_volume": certificates_volume,
            "public_url": "https://" + config.domain,
        }
        self.register_outputs(self.settings)
