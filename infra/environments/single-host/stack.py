"""Compose the shared host with its service-owned public routes."""

import pulumi
from boxie_infra.access import OperatorConfig, provision_access
from host_infra.host import HostConfig, provision
from playground_infra.dns import DnsConfig, provision_dns

config = pulumi.Config()
host = provision(
    HostConfig(
        ssh_public_key=config.get("sshPublicKey"),
        ssh_cidrs=config.require_object("sshCidrs"),
        location=config.get("location") or "nbg1",
        server_type=config.get("serverType") or "cx33",
        disk_size=config.get_int("diskSize") or 80,
        protect=config.get_bool("protect") is not False,
    )
)
provision_dns(
    DnsConfig(
        domain=config.require("domain"), cloudflare_zone_id=config.require("cloudflareZoneId")
    ),
    host.ipv4_address,
)
provision_access(
    OperatorConfig(
        account_id=config.require("cloudflareAccountId"),
        zone_id=config.require("cloudflareZoneId"),
        grafana_hostname=config.require("grafanaHostname"),
        temporal_hostname=config.require("temporalHostname"),
        team_name=config.require("operatorTeamName"),
        emails=config.require_object("operatorEmails"),
    )
)
