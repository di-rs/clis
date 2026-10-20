"""Installation writes private product settings and preserves other profiles."""

import json
from pathlib import Path

import pytest
from cli_playground_infra.installation import render as render_product
from cli_playground_infra.installation import write_environment


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
        "supervisor.secret",
        "postgres-password",
        "boxie-password",
        "temporal-password",
        "visibility-password",
        "grafana-admin",
    ):
        (credentials / name).write_text("1" * 64)
    (credentials / "profiles.json").write_text(
        json.dumps(
            {
                "other": {"commands": [["other"]]},
                "cli-playground": {"image_digest": "old", "commands": [["custom"]]},
            }
        )
    )
    for name in (
        "BOXIE_IMAGE",
        "OBS_COLLECTOR_IMAGE",
        "OBS_JOURNAL_GROUP_GID",
        "OPERATOR_GRAFANA_HOSTNAME",
        "CLI_PLAYGROUND_IMAGE",
        "CLI_PLAYGROUND_DOMAIN",
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
    profiles_before = (credentials / "profiles.json").read_bytes()
    render_product(tmp_path)
    token = tmp_path / "cli-playground/preview-token"
    assert len(token.read_text().strip()) == 64
    assert token.stat().st_mode & 0o077 == 0
    assert (credentials / "profiles.json").read_bytes() == profiles_before
    first_token = token.read_bytes()
    render_product(tmp_path)
    assert token.read_bytes() == first_token
    assert (credentials / "consumer.secret").read_bytes() == before
    assert not (tmp_path / "boxie.env").exists()
    assert json.loads((credentials / "profiles.json").read_text())["cli-playground"][
        "commands"
    ] == [["custom"]]
    product = (tmp_path / "cli-playground.env").read_text()
    assert "CLI_PLAYGROUND_DATABASE_NETWORK='boxie-postgres_playground'" in product
    assert "CLI_PLAYGROUND_BOXIE_URL='http://boxie-api:8090'" in product
    assert "CLI_PLAYGROUND_DEPLOY_TOKEN='" + token.read_text().strip() + "'" in product
    assert (tmp_path / "cli-playground.env").stat().st_mode & 0o077 == 0
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


@pytest.mark.parametrize("invalid", ["empty", "public", "symlink", "directory"])
def test_preview_token_rejects_invalid_existing_credential(tmp_path: Path, invalid: str) -> None:
    token = tmp_path / "cli-playground/preview-token"
    token.parent.mkdir(mode=0o700)
    if invalid == "directory":
        token.mkdir(mode=0o700)
    elif invalid == "symlink":
        original = tmp_path / "original"
        original.write_text("secret")
        original.chmod(0o600)
        token.symlink_to(original)
    else:
        token.write_text("" if invalid == "empty" else "secret")
        token.chmod(0o600 if invalid == "empty" else 0o644)
    with pytest.raises(ValueError, match="Preview credential"):
        render_product(tmp_path)
    assert not (tmp_path / "cli-playground.env").exists()
