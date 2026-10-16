"""Declare exactly two Access applications, a local tunnel and owned DNS records.

Outputs are inert values. No callback writes files or activates a tunnel.
"""

import base64
import binascii
import json
import re
from dataclasses import dataclass
from uuid import UUID

import pulumi
import pulumi_cloudflare as cloudflare
import yaml

_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
_NETWORK = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}\Z")


@dataclass(frozen=True)
class OperatorIngressConfig:
    team_name: str
    grafana_hostname: str
    temporal_hostname: str
    allowed_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.team_name):
            raise ValueError("invalid Access team name")
        hosts = (self.grafana_hostname, self.temporal_hostname)
        if any(len(h) > 253 or not _HOST.fullmatch(h) for h in hosts) or hosts[0] == hosts[1]:
            raise ValueError("two distinct exact hostnames required")
        if (
            not isinstance(self.allowed_identities, tuple)
            or not 1 <= len(self.allowed_identities) <= 100
            or len(set(self.allowed_identities)) != len(self.allowed_identities)
        ):
            raise ValueError("finite unique explicit email identities required")
        for email in self.allowed_identities:
            local, sep, domain = email.partition("@")
            if (
                not sep
                or not re.fullmatch(r"[a-zA-Z0-9_.+-]{1,64}", local)
                or not _HOST.fullmatch(domain)
            ):
                raise ValueError("exact email identity required")


@dataclass(frozen=True)
class OperatorOrigin:
    url: str
    network: str

    def __post_init__(self) -> None:
        if self.url not in ("http://grafana:3000", "http://temporal-ui:8080"):
            raise ValueError("only owner-supplied private dashboard aliases are supported")
        if not _NETWORK.fullmatch(self.network):
            raise ValueError("invalid dashboard network handle")


@dataclass(frozen=True)
class OperatorOrigins:
    grafana: OperatorOrigin
    temporal: OperatorOrigin

    def __post_init__(self) -> None:
        if (
            self.grafana.url != "http://grafana:3000"
            or self.temporal.url != "http://temporal-ui:8080"
        ):
            raise ValueError("origin assigned to wrong dashboard")
        if self.grafana.network == self.temporal.network:
            raise ValueError("dashboard origin networks must be separate")


@dataclass(frozen=True)
class OperatorIngressOutputs:
    tunnel_id: pulumi.Output[str]
    grafana_audience: pulumi.Output[str]
    temporal_audience: pulumi.Output[str]
    cloudflared_config: pulumi.Output[str]
    tunnel_credentials_json: pulumi.Output[str]


def _credential_path(value: str) -> None:
    if not re.fullmatch(r"/run/secrets/[a-zA-Z0-9_-]+\.json", value):
        raise ValueError("explicit /run/secrets/*.json container reference required")


def _tunnel_id(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid derived tunnel ID") from exc
    return value


def _audience(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,256}", value):
        raise ValueError("required Access audience missing or invalid")
    return value


def _secret(value: str) -> str:
    try:
        if len(base64.b64decode(value, validate=True)) < 32:
            raise ValueError
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("tunnel secret must encode at least 32 bytes") from exc
    return value


def render_cloudflared(
    config: OperatorIngressConfig,
    origins: OperatorOrigins,
    tunnel_id: str,
    grafana_audience: str,
    temporal_audience: str,
    credentials_file: str,
) -> str:
    """Pure configuration transform, never secret materialization or activation."""
    _credential_path(credentials_file)
    audiences = (_audience(grafana_audience), _audience(temporal_audience))
    if audiences[0] == audiences[1]:
        raise ValueError("Access application audiences must be distinct")
    routes = [
        {
            "hostname": hostname,
            "service": origin.url,
            "originRequest": {
                "access": {
                    "required": True,
                    "teamName": config.team_name,
                    "audTag": [audience],
                }
            },
        }
        for hostname, origin, audience in zip(
            (config.grafana_hostname, config.temporal_hostname),
            (origins.grafana, origins.temporal),
            audiences,
            strict=True,
        )
    ]
    routes.append({"service": "http_status:404"})
    return yaml.safe_dump(
        {"tunnel": _tunnel_id(tunnel_id), "credentials-file": credentials_file, "ingress": routes},
        sort_keys=False,
    )


def provision_operator_access(
    config: OperatorIngressConfig,
    origins: OperatorOrigins,
    *,
    account_id: str,
    zone_id: str,
    tunnel_name: str,
    tunnel_secret: pulumi.Input[str],
    credentials_file: str,
    provider: cloudflare.Provider | None = None,
) -> OperatorIngressOutputs:
    """Return declarations only; caller owns credentials, networks and activation."""
    for value in (account_id, zone_id):
        if not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError("explicit Cloudflare account and zone IDs required")
    if not _NAME.fullmatch(tunnel_name):
        raise ValueError("invalid tunnel name")
    _credential_path(credentials_file)
    if isinstance(tunnel_secret, str):
        _secret(tunnel_secret)
    secret = pulumi.Output.secret(tunnel_secret).apply(_secret)
    opts = pulumi.ResourceOptions(provider=provider)
    apps = [
        cloudflare.ZeroTrustAccessApplication(
            f"operator-{key}",
            account_id=account_id,
            name=f"Operator {key}",
            type="self_hosted",
            domain=hostname,
            destinations=[
                cloudflare.ZeroTrustAccessApplicationDestinationArgs(type="public", uri=hostname)
            ],
            policies=[
                cloudflare.ZeroTrustAccessApplicationPolicyArgs(
                    name="Approved operators",
                    decision="allow",
                    precedence=1,
                    includes=[
                        cloudflare.ZeroTrustAccessApplicationPolicyIncludeArgs(
                            email=cloudflare.ZeroTrustAccessApplicationPolicyIncludeEmailArgs(
                                email=email
                            ),
                        )
                        for email in config.allowed_identities
                    ],
                )
            ],
            options_preflight_bypass=False,
            opts=opts,
        )
        for key, hostname in (
            ("grafana", config.grafana_hostname),
            ("temporal", config.temporal_hostname),
        )
    ]
    tunnel = cloudflare.ZeroTrustTunnelCloudflared(
        "operator-dashboards",
        account_id=account_id,
        name=tunnel_name,
        config_src="local",
        tunnel_secret=secret,
        opts=opts,
    )
    tunnel_id = tunnel.id.apply(_tunnel_id)
    for key, hostname in (
        ("grafana", config.grafana_hostname),
        ("temporal", config.temporal_hostname),
    ):
        cloudflare.DnsRecord(
            f"operator-{key}",
            zone_id=zone_id,
            name=hostname,
            type="CNAME",
            content=tunnel_id.apply(lambda value: f"{value}.cfargotunnel.com"),
            ttl=1,
            proxied=True,
            opts=opts,
        )
    grafana_aud, temporal_aud = (app.aud.apply(_audience) for app in apps)
    rendered = pulumi.Output.all(tunnel_id, grafana_aud, temporal_aud).apply(
        lambda values: render_cloudflared(
            config, origins, values[0], values[1], values[2], credentials_file
        )
    )
    credentials: pulumi.Output[str] = pulumi.Output.secret(
        pulumi.Output.all(tunnel_id, secret).apply(
            lambda values: json.dumps(
                {"AccountTag": account_id, "TunnelID": values[0], "TunnelSecret": values[1]}
            )
        )
    )
    return OperatorIngressOutputs(tunnel_id, grafana_aud, temporal_aud, rendered, credentials)
