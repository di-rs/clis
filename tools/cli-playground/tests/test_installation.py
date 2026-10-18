"""Installation writes private product settings and preserves other profiles."""

import json
from pathlib import Path

import pytest
from boxie_infra.installation import render, write_environment
from cli_check.workspace import find_root
from playground_infra.installation import render as render_product
from playground_infra.installation import update_profile

ROOT = find_root(Path(__file__).resolve().parent)


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
    (credentials / "profiles.json").write_text(
        json.dumps(
            {
                "other": {"commands": [["other"]]},
                "playground": {"image_digest": "old", "commands": [["custom"]]},
            }
        )
    )
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
    before = (credentials / "consumer.secret").read_bytes()
    render_product(tmp_path, ROOT)
    assert (credentials / "consumer.secret").read_bytes() == before
    assert not (tmp_path / "boxie.env").exists()
    assert json.loads((credentials / "profiles.json").read_text())["playground"]["commands"] == [
        ["custom"]
    ]
    product = (tmp_path / "playground.env").read_text()
    assert "PLAYGROUND_BOXIE_URL='http://boxie-api:8090'" in product
    assert "PLAYGROUND_DEPLOY_TOKEN='" + "1" * 64 + "'" in product
    assert (tmp_path / "playground.env").stat().st_mode & 0o077 == 0
    assert json.loads((credentials / "profiles.json").read_text())["other"] == {
        "commands": [["other"]]
    }


def test_environment_replaces_symlink_without_overwriting_target(tmp_path: Path) -> None:
    target = tmp_path / "unrelated"
    target.write_text("keep")
    output = tmp_path / "private.env"
    output.symlink_to(target)
    write_environment(output, {"TOKEN": "private"})
    assert target.read_text() == "keep"
    assert not output.is_symlink()
    assert output.stat().st_mode & 0o777 == 0o600


def test_profile_seed_reads_fixed_owner_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "owner"
    (owner / "profiles").mkdir(parents=True)
    (owner / "profiles/playground.json").write_text(
        json.dumps({"playground": {"commands": [["shell"]], "image_digest": "old"}})
    )
    config = tmp_path / "config"
    (config / "boxie").mkdir(parents=True)
    monkeypatch.setenv("PLAYGROUND_SANDBOX_IMAGE", "new-digest")
    update_profile(config, owner)
    assert json.loads((config / "boxie/profiles.json").read_text()) == {
        "playground": {"commands": [["shell"]], "image_digest": "new-digest"}
    }


def test_boxie_render_does_not_require_product_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = tmp_path / "boxie"
    credentials.mkdir()
    for name in (
        "host-id",
        "installation-id",
        "postgres-password",
        "boxie-password",
        "temporal-password",
        "visibility-password",
    ):
        (credentials / name).write_text("value")
    for name in (
        "BOXIE_RUNTIME_IMAGE",
        "OBS_COLLECTOR_IMAGE",
        "OBS_JOURNAL_GROUP_GID",
        "OPERATOR_GRAFANA_HOSTNAME",
        "OPERATOR_TUNNEL_ID",
        "OPERATOR_TEAM_NAME",
        "OPERATOR_TEMPORAL_HOSTNAME",
        "OPERATOR_GRAFANA_AUDIENCE",
        "OPERATOR_TEMPORAL_AUDIENCE",
    ):
        monkeypatch.setenv(name, "setting")
    for name in ("PLAYGROUND_IMAGE", "PLAYGROUND_DOMAIN", "CLOUDFLARE_DNS_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    owner = tmp_path / "unpacked owner"
    (owner / "observability").mkdir(parents=True)
    (owner / "ingress").mkdir()
    (owner / "observability/prometheus.yml").write_text("installation: ${BOXIE_INSTALLATION_ID}\n")
    (owner / "ingress/cloudflared.yml").write_text("hostname: ${OPERATOR_GRAFANA_HOSTNAME}\n")
    render(tmp_path, owner)
    assert (tmp_path / "boxie.env").is_file()
    assert not (tmp_path / "playground.env").exists()
