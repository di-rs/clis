"""Reusable playground fixtures; no live Docker daemon is needed."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playground import app as playground_app
from playground.app import create_app
from playground.config import Settings

from .boxie_fake import FakeBoxie


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    static_dir = tmp_path / "frontend" / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text(
        '<html><script type="module" src="/static/assets/app-test.js"></script></html>'
    )
    (static_dir / "assets" / "app-test.js").write_text("document.title = 'Playground';")
    monkeypatch.setattr(playground_app, "STATIC_DIR", static_dir)
    settings = Settings(
        data_dir=tmp_path / "data",
        public_url="https://play.example",
        deploy_token="test-deployment-token",
        boxie_url="http://boxie.internal",
        boxie_credential_file=tmp_path / "boxie.secret",
        max_binary_bytes=128,
    )
    with TestClient(
        create_app(settings, boxie=FakeBoxie()), base_url=settings.public_url
    ) as client:
        yield client


@pytest.fixture
def docker_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        public_url="https://play.example",
        deploy_token="test-deployment-token",
        boxie_url="http://boxie.internal",
        boxie_credential_file=tmp_path / "boxie.secret",
    )


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("PLAYGROUND_"):
            monkeypatch.delenv(name)


@pytest.fixture
def configured_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name, value in {
        "PLAYGROUND_DATA_DIR": str(tmp_path / "data"),
        "PLAYGROUND_PUBLIC_URL": "https://play.example/",
        "PLAYGROUND_DEPLOY_TOKEN": "private-deployment-token",
        "PLAYGROUND_BOXIE_URL": "http://boxie.internal",
        "PLAYGROUND_BOXIE_CREDENTIAL_FILE": str(tmp_path / "boxie.secret"),
    }.items():
        monkeypatch.setenv(name, value)
