"""Compose the shared host with its service-owned public routes."""

import runpy
from pathlib import Path

import pulumi
from boxie_infra.access import OperatorConfig, provision_access
from host_infra.host import HostConfig, provision_resources
from playground_infra.dns import DnsConfig, provision_dns

config = pulumi.Config()
host = provision_resources(
    HostConfig(
        ssh_public_key=config.get("sshPublicKey"),
        ssh_cidrs=config.require_object("sshCidrs"),
        location=config.get("location") or "nbg1",
        server_type=config.get("serverType") or "cx33",
        disk_size=config.get_int("diskSize") or 80,
        protect=config.get_bool("protect") is not False,
    )
)
dns = provision_dns(
    DnsConfig(
        domain=config.require("domain"), cloudflare_zone_id=config.require("cloudflareZoneId")
    ),
    host.server.ipv4_address,
)
operators = provision_access(
    OperatorConfig(
        account_id=config.require("cloudflareAccountId"),
        zone_id=config.require("cloudflareZoneId"),
        grafana_hostname=config.require("grafanaHostname"),
        temporal_hostname=config.require("temporalHostname"),
        team_name=config.require("operatorTeamName"),
        emails=config.require_object("operatorEmails"),
    )
)

# Provision-only previews remain possible; installation is an explicit platform operation.
if config.get_bool("install"):
    installer = runpy.run_path(str(Path(__file__).with_name("installation.py")))
    settings = pulumi.Output.all(
        **operators.settings,
        PLAYGROUND_DOMAIN=config.require("domain"),
        ACME_EMAIL=config.require("acmeEmail"),
        CLOUDFLARE_DNS_API_TOKEN=config.require_secret("cloudflareDnsApiToken"),
    ).apply(installer["serialize_settings"])
    installer["provision_installation"](
        host.connection,
        Path(config.require("sourceDirectory")).resolve(),
        config.require("revision"),
        config.require("runId"),
        host_id=host.server.id,
        settings=settings,
        tunnel_credentials=operators.credentials,
        registry_config=config.require_secret("registryConfig"),
        dependencies=[host.server, host.volume_attachment, *dns, *operators.resources],
    )
