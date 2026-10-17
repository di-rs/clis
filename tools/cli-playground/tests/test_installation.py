"""Installation writes private product settings and preserves other profiles."""

import json
from pathlib import Path

import pytest
from boxie_infra.installation import render, write_environment

ROOT = Path(__file__).resolve().parents[3]


def test_environment_rejects_shell_injection(tmp_path: Path) -> None:
    output = tmp_path / "private.env"
    with pytest.raises(ValueError):
        write_environment(output, {"TOKEN": "value\nEVIL=1"})
    assert not output.exists()


def test_owner_settings_use_private_credentials_and_preserve_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = tmp_path / "boxie"
    credentials.mkdir()
    for name in (
        "host-id",
        "installation-id",
        "consumer.secret",
        "orchestrator.secret",
        "postgres-password",
        "boxie-password",
        "temporal-password",
        "visibility-password",
        "grafana-admin",
        "preview-token",
    ):
        (credentials / name).write_text("1" * 64)
    (credentials / "profiles.json").write_text(json.dumps({"other": {"commands": [["other"]]}}))
    for name in (
        "BOXIE_RUNTIME_IMAGE",
        "OBS_COLLECTOR_IMAGE",
        "OBS_JOURNAL_GROUP_GID",
        "OPERATOR_GRAFANA_HOSTNAME",
        "PLAYGROUND_IMAGE",
        "PLAYGROUND_DOMAIN",
        "PLAYGROUND_SANDBOX_IMAGE",
        "ACME_EMAIL",
        "CLOUDFLARE_DNS_API_TOKEN",
        "OPERATOR_TUNNEL_ID",
        "OPERATOR_TEAM_NAME",
        "OPERATOR_TEMPORAL_HOSTNAME",
        "OPERATOR_GRAFANA_AUDIENCE",
        "OPERATOR_TEMPORAL_AUDIENCE",
    ):
        monkeypatch.setenv(name, "setting")
    render(tmp_path, ROOT)
    product = (tmp_path / "playground.env").read_text()
    assert "PLAYGROUND_BOXIE_URL='http://boxie-api:8090'" in product
    assert "PLAYGROUND_DEPLOY_TOKEN='" + "1" * 64 + "'" in product
    assert (tmp_path / "playground.env").stat().st_mode & 0o077 == 0
    assert json.loads((credentials / "profiles.json").read_text())["other"] == {
        "commands": [["other"]]
    }


def test_failed_secret_handoff_does_not_write_github(tmp_path: Path) -> None:
    import os
    import subprocess

    for name, body in (("ssh", "exit 1"), ("gh", 'touch "$MARKER"')):
        command = tmp_path / name
        command.write_text("#!/bin/sh\n" + body + "\n")
        command.chmod(0o755)
    key = tmp_path / "key"
    known = tmp_path / "known"
    key.write_text("private")
    known.write_text("host key")
    marker = tmp_path / "written"
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "infra/environments/single-host/configure-github.sh"),
            "test/repo",
            "host.example.com",
            str(known),
            str(key),
        ],
        env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"], "MARKER": str(marker)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not marker.exists()
