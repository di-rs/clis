"""Boxie-owned tunnel and deny-by-default operator applications."""

import base64
import hashlib
import json
import re

import pulumi
import pulumi_cloudflare as cloudflare
import pulumi_tls as tls
from pydantic import BaseModel, Field, field_validator, model_validator


class OperatorConfig(BaseModel):
    account_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    zone_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    grafana_hostname: str
    temporal_hostname: str
    team_name: str = Field(pattern=r"^[a-z0-9-]+$")
    emails: list[str] = Field(min_length=1)

    @field_validator("grafana_hostname", "temporal_hostname")
    @classmethod
    def hostname(cls, value: str) -> str:
        if not re.fullmatch(
            r"(?=.{1,220}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value
        ):
            raise ValueError("operator hostname must be a lowercase DNS hostname")
        return value

    @field_validator("emails")
    @classmethod
    def addresses(cls, value: list[str]) -> list[str]:
        if any("@" not in email or any(c.isspace() for c in email) for email in value):
            raise ValueError("operator emails must be explicit addresses")
        return value

    @model_validator(mode="after")
    def distinct(self) -> OperatorConfig:
        if self.grafana_hostname == self.temporal_hostname:
            raise ValueError("operator hostnames must differ")
        return self


def provision_access(config: OperatorConfig) -> None:
    key = tls.PrivateKey("operator-tunnel-secret", algorithm="ED25519")
    secret: pulumi.Output[str] = pulumi.Output.secret(
        key.private_key_openssh.apply(
            lambda value: base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
        )
    )
    tunnel = cloudflare.ZeroTrustTunnelCloudflared(
        "boxie-operators",
        account_id=config.account_id,
        name="boxie-operators",
        config_src="local",
        tunnel_secret=secret,
    )
    for name, hostname in (
        ("grafana", config.grafana_hostname),
        ("temporal", config.temporal_hostname),
    ):
        app = cloudflare.ZeroTrustAccessApplication(
            f"boxie-{name}",
            account_id=config.account_id,
            name=f"Boxie {name}",
            domain=hostname,
            type="self_hosted",
            session_duration="8h",
            policies=[
                cloudflare.ZeroTrustAccessApplicationPolicyArgs(
                    name="Operators",
                    decision="allow",
                    precedence=1,
                    includes=[
                        cloudflare.ZeroTrustAccessApplicationPolicyIncludeArgs(
                            email=cloudflare.ZeroTrustAccessApplicationPolicyIncludeEmailArgs(
                                email=email
                            )
                        )
                        for email in config.emails
                    ],
                )
            ],
        )
        cloudflare.DnsRecord(
            f"boxie-{name}",
            zone_id=config.zone_id,
            name=hostname,
            type="CNAME",
            content=tunnel.id.apply(lambda value: f"{value}.cfargotunnel.com"),
            ttl=1,
            proxied=True,
        )
        pulumi.export(f"{name}Audience", app.aud)
    pulumi.export("operatorTunnelId", tunnel.id)
    pulumi.export(
        "operatorTunnelCredentials",
        pulumi.Output.secret(
            pulumi.Output.all(tunnel.id, secret).apply(
                lambda values: json.dumps(
                    {
                        "AccountTag": config.account_id,
                        "TunnelID": values[0],
                        "TunnelSecret": values[1],
                    }
                )
            )
        ),
    )
