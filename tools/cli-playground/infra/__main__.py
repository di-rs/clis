"""Run with pulumi preview/up from this directory after root uv sync."""

import sys
from pathlib import Path

import pulumi

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from infra.host import HostConfig, provision  # noqa: E402

config = pulumi.Config()

provision(
    HostConfig(
        domain=config.require("domain"),
        cloudflare_zone_id=config.require("cloudflareZoneId"),
        ssh_public_key=config.get("sshPublicKey") or None,
        ssh_cidrs=config.get_object("sshCidrs", default=["0.0.0.0/0"]),
        location=config.get("location") or "nbg1",
        server_type=config.get("serverType") or "cx33",
        backups=config.get_bool("backups") or False,
        protect=config.get_bool("protect") is not False,
    )
)
