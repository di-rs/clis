"""Setup inputs are rendered privately without provider transport resources."""

import runpy
from pathlib import Path

import pytest
from cli_check.workspace import find_root

ROOT = find_root(Path(__file__).resolve().parent)


def test_render_inputs_writes_private_settings_and_tunnel_without_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    render = runpy.run_path(str(ROOT / "infra/environments/single-host/installation.py"))[
        "render_inputs"
    ]
    render(
        {"PLAYGROUND_DOMAIN": "play.example.com", "ACME_EMAIL": "ops@example.com"},
        '{"AccountTag":"account","TunnelID":"tunnel","TunnelSecret":"private"}',
        tmp_path,
    )
    assert "PLAYGROUND_DOMAIN='play.example.com'" in (tmp_path / "settings.env").read_text()
    assert '"TunnelSecret":"private"' in (tmp_path / "tunnel.json").read_text()
    assert all(
        (tmp_path / name).stat().st_mode & 0o077 == 0 for name in ("settings.env", "tunnel.json")
    )
    assert capsys.readouterr() == ("", "")


def test_render_inputs_rejects_newline_without_writing_secrets(tmp_path: Path) -> None:
    render = runpy.run_path(str(ROOT / "infra/environments/single-host/installation.py"))[
        "render_inputs"
    ]
    with pytest.raises(ValueError):
        render({"PLAYGROUND_DOMAIN": "safe\nBAD=1"}, "{}", tmp_path)
    assert not list(tmp_path.iterdir())


def test_render_inputs_never_overwrites_existing_private_file(tmp_path: Path) -> None:
    render = runpy.run_path(str(ROOT / "infra/environments/single-host/installation.py"))[
        "render_inputs"
    ]
    existing = tmp_path / "settings.env"
    existing.write_text("keep\n")
    with pytest.raises(FileExistsError):
        render(
            {"PLAYGROUND_DOMAIN": "play.example.com"},
            '{"AccountTag":"account","TunnelID":"tunnel","TunnelSecret":"private"}',
            tmp_path,
        )
    assert existing.read_text() == "keep\n"
    assert not (tmp_path / "tunnel.json").exists()
