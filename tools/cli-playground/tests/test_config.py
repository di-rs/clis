"""Settings validate environment input before the gateway starts."""

import pytest
from playground.config import Settings
from playground.storage import EnvironmentStore
from pydantic import ValidationError


@pytest.mark.usefixtures("configured_environment")
def test_data_directory_requires_an_absolute_mount_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYGROUND_DATA_DIR", "relative")
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
    assert settings.preview_url(identifier) == (
        "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.play.example/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert settings.accepts_host("play.example")
    assert settings.accepts_host(f"{identifier}.play.example", identifier)
    assert not settings.accepts_host("b" * 32 + ".play.example", identifier)
    assert not settings.accepts_host(f"{identifier}.play.example.evil.test", identifier)


@pytest.mark.usefixtures("configured_environment")
def test_empty_preview_domain_keeps_path_based_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYGROUND_PREVIEW_DOMAIN", "")
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
