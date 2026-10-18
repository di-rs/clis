"""Compatibility entry point; shared host and product DNS have separate owners."""

import pulumi_hcloud as hcloud
from host_infra.host import HostConfig as SharedHostConfig
from host_infra.host import provision as provision_host

from playground_infra.dns import DnsConfig, provision_dns


class HostConfig(SharedHostConfig, DnsConfig):
    pass


def provision(config: HostConfig) -> hcloud.Server:
    server = provision_host(
        SharedHostConfig.model_validate(config.model_dump(exclude={"domain", "cloudflare_zone_id"}))
    )
    provision_dns(config, server.ipv4_address)
    return server
