"""Render private setup inputs after Pulumi has provisioned the host."""

import json
import os
import re
from pathlib import Path


def serialize_settings(values: dict[str, str]) -> str:
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z_]+", key) or any(char in value for char in "\n\r\x00'$`\\"):
            raise ValueError(f"Invalid installation setting: {key}")
    return "".join(f"{key}='{value}'\n" for key, value in values.items())


def render_inputs(settings: dict[str, str], tunnel_credentials: str, target: Path) -> None:
    """Validate and write mode-0600 setup files without logging their contents."""
    environment = serialize_settings(settings)
    credentials = json.loads(tunnel_credentials)
    if (
        not isinstance(credentials, dict)
        or set(credentials)
        != {
            "AccountTag",
            "TunnelID",
            "TunnelSecret",
        }
        or not all(isinstance(value, str) and value for value in credentials.values())
    ):
        raise ValueError("Invalid tunnel credentials")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, content in (
        ("settings.env", environment),
        ("tunnel.json", json.dumps(credentials, separators=(",", ":")) + "\n"),
    ):
        destination = target / name
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(content)
