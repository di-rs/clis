"""Settings validate environment input before the backend starts."""

from pathlib import Path

import pytest
from cli_playground.config import Settings
from cli_playground.storage import EnvironmentStore
from pydantic import ValidationError


@pytest.mark.usefixtures("configured_environment")
def test_consumer_cannot_configure_a_boxie_profile() -> None:
    with pytest.raises(ValidationError, match="boxie_profile"):
        Settings.model_validate({"boxie_profile": "custom"})


@pytest.mark.usefixtures("configured_environment")
def test_data_directory_requires_an_absolute_mount_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLI_PLAYGROUND_DATA_DIR", "relative")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.usefixtures("configured_environment")
@pytest.mark.parametrize(
    "origin",
    [
        "https://user:password@play.example",
        "https://play.example/path",
    ],
)
def test_public_url_rejects_non_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(public_url=origin)


@pytest.mark.usefixtures("configured_environment")
def test_preview_domain_requires_https() -> None:
    with pytest.raises(ValidationError):
        Settings(public_url="http://play.example", preview_domain="play.example")


@pytest.mark.usefixtures("configured_environment")
def test_preview_urls_and_hosts_remain_scoped_to_one_preview() -> None:
    settings = Settings(preview_domain="play.example")
    identifier = "a" * 32
    assert settings.preview_url(identifier) == f"https://play.example/p/{identifier}"
    assert settings.accepts_host("play.example")
    assert not settings.accepts_host(f"{identifier}.play.example", identifier)
    assert not settings.accepts_host("b" * 32 + ".play.example", identifier)
    assert not settings.accepts_host(f"{identifier}.play.example.evil.test", identifier)


@pytest.mark.usefixtures("configured_environment")
def test_empty_preview_domain_keeps_path_based_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLI_PLAYGROUND_PREVIEW_DOMAIN", "")
    settings = Settings()
    assert settings.preview_url("a" * 32) == (
        "https://play.example/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )


@pytest.mark.usefixtures("configured_environment")
def test_readable_pr_hosts_bind_to_the_correct_preview() -> None:
    settings = Settings(preview_domain="play.example")
    identifier = EnvironmentStore.identifier(123)
    assert settings.preview_url(identifier, pr=123) == f"https://pr-123.play.example/p/{identifier}"
    assert settings.accepts_host("pr-123.play.example", identifier)
    assert not settings.accepts_host("pr-124.play.example", identifier)
    assert not settings.accepts_host("pr-0123.play.example", identifier)


@pytest.mark.usefixtures("configured_environment")
def test_composition_root_rejects_symlinked_consumer_credentials(tmp_path: Path) -> None:
    from cli_playground.app import create_app

    secret = tmp_path / "actual-secret"
    secret.write_text("private-boxie-consumer-secret")
    secret.chmod(0o600)
    link = tmp_path / "linked-secret"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="private regular"):
        create_app(Settings(boxie_credential_file=link))


@pytest.mark.parametrize("invalid", ["public", "symlink", "empty"])
def test_database_secret_rejects_invalid_file(tmp_path: Path, invalid: str) -> None:
    from cli_playground.database import read_dsn

    secret = tmp_path / "database"
    secret.write_text("" if invalid == "empty" else "host=localhost dbname=cli_playground")
    secret.chmod(0o644 if invalid == "public" else 0o600)
    if invalid == "symlink":
        link = tmp_path / "link"
        link.symlink_to(secret)
        secret = link
    with pytest.raises((ValueError, OSError)):
        read_dsn(secret)
