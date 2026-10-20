"""Render private cli-playground settings and its own preview credential."""

import argparse
import os
import secrets
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


def render(root: Path) -> None:
    credentials = root / "boxie"

    token = root / "cli-playground/preview-token"
    token.parent.mkdir(mode=0o700, exist_ok=True)
    if not token.exists():
        descriptor = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_hex(32) + "\n")
    if (
        token.is_symlink()
        or not token.is_file()
        or token.stat().st_mode & 0o077
        or not token.read_text().strip()
    ):
        raise ValueError("Preview credential must be a private nonempty regular file")

    write_environment(
        root / "cli-playground.env",
        {
            "CLI_PLAYGROUND_IMAGE": os.environ["CLI_PLAYGROUND_IMAGE"],
            "CLI_PLAYGROUND_DOMAIN": os.environ["CLI_PLAYGROUND_DOMAIN"],
            "CLI_PLAYGROUND_DEPLOY_TOKEN": token.read_text().strip(),
            "CLI_PLAYGROUND_BOXIE_URL": "http://boxie-api:8090",
            "CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE": str(credentials / "consumer.secret"),
            "CLI_PLAYGROUND_CONSUMER_NETWORK": "boxie-consumers",
            "CLI_PLAYGROUND_PROXY_NETWORK": "cli-playground-proxy",
            "CLI_PLAYGROUND_DATA_VOLUME": "cli-playground-data",
            "CLI_PLAYGROUND_CERTIFICATES_VOLUME": "cli-playground-certificates",
            "ACME_EMAIL": os.environ["ACME_EMAIL"],
            "CLOUDFLARE_DNS_API_TOKEN": os.environ["CLOUDFLARE_DNS_API_TOKEN"],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    root = Path(os.environ.get("SANCTUARY_CONFIG_DIR", "/etc/sanctuary"))
    render(root)


if __name__ == "__main__":
    main()
