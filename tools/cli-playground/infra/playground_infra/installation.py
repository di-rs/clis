"""Render Playground settings and its approved profile from installation inputs."""

import argparse
import json
import os
import tempfile
from pathlib import Path


def write_environment(path: Path, values: dict[str, str]) -> None:
    for name, value in values.items():
        if any(character in value for character in "\n\r\x00'$`\\"):
            raise ValueError(f"Invalid installation setting: {name}")
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write("".join(f"{key}='{value}'\n" for key, value in values.items()))
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def render(root: Path, source: Path) -> None:
    credentials = root / "boxie"

    def secret(name: str) -> str:
        return (credentials / name).read_text().strip()

    write_environment(
        root / "playground.env",
        {
            "PLAYGROUND_IMAGE": os.environ["PLAYGROUND_IMAGE"],
            "PLAYGROUND_DOMAIN": os.environ["PLAYGROUND_DOMAIN"],
            "PLAYGROUND_DEPLOY_TOKEN": secret("preview-token"),
            "PLAYGROUND_BOXIE_URL": "http://boxie-api:8090",
            "PLAYGROUND_BOXIE_CREDENTIAL_FILE": str(credentials / "consumer.secret"),
            "PLAYGROUND_CONSUMER_NETWORK": "boxie-consumers",
            "PLAYGROUND_PROXY_NETWORK": "playground-proxy",
            "PLAYGROUND_DATA_VOLUME": "playground-data",
            "PLAYGROUND_CERTIFICATES_VOLUME": "playground-certificates",
            "ACME_EMAIL": os.environ["ACME_EMAIL"],
            "CLOUDFLARE_DNS_API_TOKEN": os.environ["CLOUDFLARE_DNS_API_TOKEN"],
        },
    )
    update_profile(root, source)


def update_profile(root: Path, source: Path) -> None:
    profiles_path = root / "boxie/profiles.json"
    profiles = json.loads(profiles_path.read_text()) if profiles_path.exists() else {}
    profile = profiles.get("playground")
    if profile is None:
        profile = json.loads(
            (source / "tools/cli-playground/infra/profiles/playground.json").read_text()
        )["playground"]
    profile["image_digest"] = os.environ["PLAYGROUND_SANDBOX_IMAGE"]
    profiles["playground"] = profile
    with tempfile.NamedTemporaryFile(mode="w", dir=profiles_path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(json.dumps(profiles, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(profiles_path)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--profile-only", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ.get("CLIS_CONFIG_DIR", "/etc/clis"))
    if args.profile_only:
        update_profile(root, args.source)
    else:
        render(root, args.source)


if __name__ == "__main__":
    main()
