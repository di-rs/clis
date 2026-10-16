#!/usr/bin/env python3
"""Configure infrastructure or deploy an existing playground from trusted CI."""

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from playground_infra import deploy
from playground_infra.config import ActivationUnavailable

INFRA = Path.cwd() / "tools/cli-playground/infra"
# Allow two minutes for state reads and storage retries, well inside the
# 25-minute deployment job deadline so timeout handling can clean up normally.
STACK_OUTPUT_TIMEOUT = 120


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or any(c in value for c in "\r\n\0"):
        raise ValueError(f"Set a nonempty single-line {name} in GitHub Actions settings")
    return value


def application_domain() -> str:
    url = urlsplit(required("PLAYGROUND_URL"))
    if (
        url.scheme != "https"
        or not url.hostname
        or url.netloc != url.hostname
        or url.path
        or url.query
        or url.fragment
    ):
        raise ValueError("PLAYGROUND_URL must be an HTTPS origin without a path or trailing slash")
    try:
        return deploy.DomainConfig.model_validate({"PLAYGROUND_DOMAIN": url.hostname}).domain
    except ValueError:
        raise ValueError("PLAYGROUND_URL must contain a valid lowercase DNS hostname") from None


def application_config() -> deploy.AppConfig:
    try:
        return deploy.AppConfig.model_validate(
            {
                "PLAYGROUND_DOMAIN": application_domain(),
                "ACME_EMAIL": required("ACME_EMAIL"),
                "PLAYGROUND_DEPLOY_TOKEN": required("PLAYGROUND_DEPLOY_TOKEN"),
                "CLOUDFLARE_DNS_API_TOKEN": required("CLOUDFLARE_DNS_API_TOKEN"),
            }
        )
    except ValueError:
        # Validation errors can include secret values.
        raise ValueError(
            "Invalid playground URL, certificate email or runtime credentials"
        ) from None


def configure_stack(path: Path) -> None:
    # Pulumi accepts an empty passphrase; a missing Actions secret must fail first.
    required("PULUMI_CONFIG_PASSPHRASE")
    required("PULUMI_BACKEND_URL")
    domain = application_domain()
    zone = required("CLOUDFLARE_ZONE_ID")
    if not re.fullmatch(r"[a-f0-9]{32}", zone):
        raise ValueError("Cloudflare zone ID must contain 32 lowercase hexadecimal characters")
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    values = data.setdefault("config", {})
    values["cli-playground:domain"] = domain
    values["cli-playground:cloudflareZoneId"] = zone
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def private_write(path: Path, data: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write(data)


def prepare(directory: Path, outputs: dict[str, Any]) -> str:
    release = deploy.Release(
        revision=required("RELEASE_REVISION"),
        gateway_image=required("GATEWAY_IMAGE"),
        sandbox_image=required("SANDBOX_IMAGE"),
    )
    if release.revision != required("GITHUB_SHA"):
        raise ValueError("Release revision must match the workflow commit")
    config = application_config()
    if outputs["publicUrl"] != f"https://{config.domain}":
        raise ValueError("Configured playground URL differs from the provisioned stack")
    host = str(ipaddress.IPv4Address(outputs["ipv4"]))
    host_key = str(outputs["sshHostPublicKey"]).strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [^\r\n]+)?", host_key):
        raise ValueError("Invalid SSH host key output")
    private_key = str(outputs["sshPrivateKey"])
    if not private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n"):
        raise ValueError("Invalid SSH identity output")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, content in {
        "identity": private_key,
        "known_hosts": f"{host} {host_key}\n",
        "app.env": "".join(f"{k}={v}\n" for k, v in config.model_dump(by_alias=True).items()),
        "release.json": release.data().decode(),
    }.items():
        private_write(directory / name, content)
    return host


def stack_outputs() -> dict[str, Any]:
    """Read existing state within its time budget; let subprocess timeouts propagate."""
    # No provider login, refresh or update.
    result = subprocess.run(
        ["pulumi", "stack", "output", "--json", "--show-secrets", "--stack", "production"],
        cwd=INFRA,
        capture_output=True,
        text=True,
        check=False,
        timeout=STACK_OUTPUT_TIMEOUT,
    )
    if result.returncode:
        raise RuntimeError("Could not read Pulumi state; run setup first")
    outputs = json.loads(result.stdout)
    if not isinstance(outputs, dict):
        raise RuntimeError("Invalid Pulumi stack outputs")
    return outputs


def wait_for_ssh(
    host: str,
    directory: Path,
    timeout: float = 600,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    command = deploy.ssh_command(host, directory / "identity", directory / "known_hosts")
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            result = subprocess.run(
                [*command, "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
            if result.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        sleep(min(5, max(0, deadline - clock())))
    raise RuntimeError("SSH did not become ready with the pinned host identity")


def deploy_runtime() -> tuple[str, str]:
    raise ActivationUnavailable()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["configure", "deploy"])
    parser.add_argument("--config-file", type=Path, default=INFRA / "Pulumi.production.yaml")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            configure_stack(args.config_file)
        else:
            revision, health = deploy_runtime()
            print(f"Deployed {revision}; verified {health}")
    except ActivationUnavailable as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            f"Playground CI {args.command} failed ({type(error).__name__}). Check configuration and deployment logs.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
