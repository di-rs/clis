"""Product-owned public DNS routes."""

import re

import pulumi
import pulumi_cloudflare as cloudflare
from pydantic import BaseModel, Field, field_validator


class DnsConfig(BaseModel):
    domain: str
    cloudflare_zone_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    @field_validator("domain")
    @classmethod
    def dns_hostname(cls, value: str) -> str:
        labels = value.split(".")
        if (
            len(value) > 220
            or len(labels) < 2
            or not re.fullmatch(r"[a-z]{2,63}", labels[-1])
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            )
        ):
            raise ValueError("domain must be a lowercase DNS hostname, e.g. play.example.com")
        return value


def provision_dns(config: DnsConfig, ipv4: pulumi.Input[str]) -> list[pulumi.Resource]:
    resources: list[pulumi.Resource] = []
    for name, domain in (
        ("cli-playground", config.domain),
        ("cli-playground-previews", f"*.{config.domain}"),
    ):
        record = cloudflare.DnsRecord(
            name,
            zone_id=config.cloudflare_zone_id,
            name=domain,
            type="A",
            content=ipv4,
            ttl=300,
            proxied=False,
        )
        resources.append(record)
    pulumi.export("publicUrl", f"https://{config.domain}")
    return resources
