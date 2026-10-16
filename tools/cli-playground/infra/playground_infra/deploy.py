#!/usr/bin/env python3.15
"""Deploy a CI-built playground release to a bootstrapped VPS over verified SSH."""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from playground_infra.config import ActivationUnavailable

# Transitional source helpers are checkout-only; invoke from the repository root.
ROOT = Path.cwd()
REMOTE_ROOT = "/opt/cli-playground"
REMOTE_CONFIG = "/etc/cli-playground"
REMOTE_LOG = "/var/log/cli-playground-deploy.log"
HEALTH_TIMEOUT = 180
MAX_ENV_BYTES = 64 * 1024
MAX_RELEASE_BYTES = 4096
DEPLOY_FILES = (
    "tools/cli-playground/infra/compose.production.yml",
    "tools/cli-playground/infra/routes.yml",
    "tools/cli-playground/infra/deploy.sh",
)
DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
IMAGE_PATH = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"


class DomainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    domain: str = Field(
        alias="PLAYGROUND_DOMAIN",
        max_length=220,
        pattern=rf"^(?:{DNS_LABEL}\.)+[a-z]{{2,63}}\z",
    )


class AppConfig(DomainConfig):
    acme_email: str = Field(alias="ACME_EMAIL", pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+\z")
    deploy_token: str = Field(alias="PLAYGROUND_DEPLOY_TOKEN", min_length=1, repr=False)
    dns_api_token: str = Field(alias="CLOUDFLARE_DNS_API_TOKEN", min_length=1, repr=False)

    @field_validator("*")
    @classmethod
    def literal_compose_value(cls, value: str) -> str:
        if re.search(r"[\s$'\"\\#]", value):
            raise ValueError("app.env values must be unquoted without whitespace or interpolation")
        return value


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    revision: str = Field(pattern=r"^[0-9a-f]{40}\z")
    gateway_image: str = Field(max_length=512)
    sandbox_image: str = Field(max_length=512)

    @field_validator("gateway_image", "sandbox_image")
    @classmethod
    def image_digest(cls, value: str) -> str:
        pattern = (
            rf"{DNS_LABEL}(?:\.{DNS_LABEL})*(?::[0-9]{{1,5}})?/"
            rf"{IMAGE_PATH}(?:/{IMAGE_PATH})*@sha256:[0-9a-f]{{64}}"
        )
        registry = value.partition("/")[0]
        host, _, port = registry.partition(":")
        if (
            not re.fullmatch(pattern, value)
            or len(host) > 253
            or ("." not in host and host != "localhost" and not port)
            or (port and not 1 <= int(port) <= 65535)
        ):
            raise ValueError("image must be a registry-qualified image@sha256 digest without a tag")
        return value

    def data(self) -> bytes:
        return (json.dumps(self.model_dump(), sort_keys=True, indent=2) + "\n").encode()

    def directory(self) -> str:
        return f"{self.revision}-{hashlib.sha256(self.data()).hexdigest()}"


def hostname(value: str) -> bool:
    return (
        len(value) <= 253
        and re.fullmatch(rf"{DNS_LABEL}(?:\.{DNS_LABEL})*", value, re.IGNORECASE | re.ASCII)
        is not None
    )


def validate_host(value: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not hostname(value):
            raise ValueError("host must be an IP address or DNS hostname") from None


def environment_file(path: Path) -> tuple[bytes, dict[str, str]]:
    """Read literal KEY=value records without evaluating shell or dotenv expansions."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("environment files must be regular files with mode 0600")
        data = source.read(MAX_ENV_BYTES + 1)
    if len(data) > MAX_ENV_BYTES:
        raise ValueError("environment file exceeds size limit")
    values: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or "\0" in value
        ):
            raise ValueError("environment files require unique literal KEY=value records")
        values[key] = value
    if not values:
        raise ValueError("environment file is empty")
    return data, values


def committed_revision(paths: tuple[Path, ...]) -> str:
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("expected a committed Git revision")
    for path in paths:
        try:
            relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            continue
        tracked = subprocess.run(
            ["git", "cat-file", "-e", f"{revision}:{relative}"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if tracked.returncode == 0:
            raise ValueError("environment files must not be tracked in the deployed revision")
    return revision


def read_release(path: Path) -> Release:
    """Accept only immutable image references from the reviewed CI release artifact."""

    def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("release manifest contains duplicate fields")
            result[key] = value
        return result

    with path.open("rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("release manifest must be a regular file")
        data = source.read(MAX_RELEASE_BYTES + 1)
    if len(data) > MAX_RELEASE_BYTES:
        raise ValueError("release manifest exceeds size limit")
    try:
        return Release.model_validate(
            json.loads(data.decode("utf-8"), object_pairs_hook=unique_fields)
        )
    except (ValueError, RecursionError) as error:
        raise ValueError(f"Invalid release manifest: {error}") from None


def remote_command(manifest: Release) -> str:
    root, config = shlex.quote(REMOTE_ROOT), shlex.quote(REMOTE_CONFIG)
    release = shlex.quote(f"{REMOTE_ROOT}/releases/{manifest.directory()}")
    # The small SSH bootstrap only unpacks stdin and invokes the committed script.
    script = f"""umask 077
install -m 0600 /dev/null {shlex.quote(REMOTE_LOG)}
exec >{shlex.quote(REMOTE_LOG)} 2>&1
test -d {root}
test -d {config}
mkdir -p {root}/releases
staging=$(mktemp -d {root}/releases/.deploy.XXXXXX)
trap 'rm -rf "$staging"' EXIT
tar -xf - -C "$staging"
bash "$staging/source/tools/cli-playground/infra/deploy.sh" "$staging" {release} {config}
"""
    locked = shlex.join(
        ["flock", "-w", "120", f"{REMOTE_ROOT}/deploy.lock", "bash", "-euc", script]
    )
    return "cloud-init status --wait >/dev/null && " + locked


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


class HealthResponse(Protocol):
    def read(self, size: int, /) -> bytes: ...


class HealthOpener(Protocol):
    def open(self, url: str, /, *, timeout: float) -> AbstractContextManager[HealthResponse]: ...


def wait_for_health(
    domain: str,
    *,
    timeout: float = HEALTH_TIMEOUT,
    opener: HealthOpener | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    url = f"https://{domain}/healthz"
    deadline = clock() + timeout
    opener = opener if opener is not None else urllib.request.build_opener(NoRedirect)
    while (remaining := deadline - clock()) > 0:
        try:
            with opener.open(url, timeout=min(5, remaining)) as response:
                payload = response.read(1025)
            if len(payload) <= 1024 and json.loads(payload) == {"status": "ok"}:
                return
        except OSError, ValueError:
            pass
        sleep(min(5, max(0, deadline - clock())))
    raise RuntimeError(
        f"Services started but public HTTPS health is not ready at {url}. "
        f"Check DNS, ACME and {REMOTE_LOG} on the server."
    )


def ssh_command(
    host: str, identity_file: Path | None = None, known_hosts_file: Path | None = None
) -> list[str]:
    validate_host(host)
    if (identity_file is None) != (known_hosts_file is None):
        raise ValueError("Provide both the SSH identity and known-hosts files")
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=15",
    ]
    if identity_file is not None:
        command.extend(
            [
                "-F",
                "/dev/null",
                "-i",
                str(identity_file),
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                f"UserKnownHostsFile={known_hosts_file}",
                "-o",
                "GlobalKnownHostsFile=/dev/null",
            ]
        )
    return [*command, "--", f"root@{host}"]


def deploy(
    host: str,
    env_file: Path,
    release_file: Path,
    *,
    identity_file: Path | None = None,
    known_hosts_file: Path | None = None,
    registry_credentials: tuple[str, str] | None = None,
) -> tuple[str, str]:
    raise ActivationUnavailable()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Verify the SSH fingerprint in known_hosts first. Env files require mode 0600. Check out the approved release commit before deploying.",
    )
    parser.add_argument("--host", required=True, help="VPS IP or hostname (root SSH)")
    parser.add_argument(
        "--env-file", required=True, type=Path, help="Private app.env (literal KEY=value)"
    )
    parser.add_argument(
        "--release-file", required=True, type=Path, help="Approved release.json from trusted CI"
    )
    args = parser.parse_args()
    try:
        revision, health_url = deploy(args.host, args.env_file, args.release_file)
    except (ValueError, RuntimeError) as error:
        sys.exit(str(error))
    except OSError, subprocess.SubprocessError:
        sys.exit(
            "Deployment failed; check private configuration files, the approved release artifact, committed configuration, SSH trust and server health."
        )
    print(f"Deployed committed revision {revision}. Health check: {health_url}")


if __name__ == "__main__":
    main()
