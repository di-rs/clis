"""Reusable playground fixtures; no live Docker daemon is needed."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from cli_playground.app import create_app
from cli_playground.service import CliPlaygroundService
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .boxie_fake import FakeBoxie
from .helpers import MutableClock, Settings


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def boxie(clock: MutableClock) -> FakeBoxie:
    return FakeBoxie(clock=clock)


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    static_dir = tmp_path / "frontend" / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text(
        '<html><script type="module" src="/static/assets/app-test.js"></script></html>'
    )
    (static_dir / "assets" / "app-test.js").write_text("document.title = 'Playground';")
    return static_dir


@pytest.fixture
def api_client(
    tmp_path: Path, clock: MutableClock, boxie: FakeBoxie, static_dir: Path
) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path / "data",
        public_url="https://play.example",
        deploy_token="test-deployment-token",
        boxie_url="http://boxie.internal",
        boxie_credential_file=tmp_path / "boxie.secret",
        max_binary_bytes=128,
    )
    with TestClient(
        create_app(settings, boxie=boxie, static_dir=static_dir, clock=clock),
        base_url=settings.public_url,
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


@pytest.fixture
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("CLI_PLAYGROUND_"):
            monkeypatch.delenv(name)


@pytest.fixture
def configured_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clean_settings_environment: None
) -> None:
    for name, value in {
        "CLI_PLAYGROUND_DATA_DIR": str(tmp_path / "data"),
        "CLI_PLAYGROUND_POSTGRES_DSN_FILE": str(tmp_path / "postgres-dsn"),
        "CLI_PLAYGROUND_PUBLIC_URL": "https://play.example/",
        "CLI_PLAYGROUND_DEPLOY_TOKEN": "private-deployment-token",
        "CLI_PLAYGROUND_BOXIE_URL": "http://boxie.internal",
        "CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE": str(tmp_path / "boxie.secret"),
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def service(api_client: TestClient) -> CliPlaygroundService:
    assert isinstance(api_client.app, FastAPI)
    service = api_client.app.state.service
    assert isinstance(service, CliPlaygroundService)
    return service


DATABASE_TESTS = {
    "test_api.py",
    "test_sessions.py",
    "test_service.py",
    "test_stop_cleanup.py",
    "test_terminal_limits.py",
    "test_reconnect.py",
    "test_boxie_integration.py",
    "test_boxie_recovery.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.parent == Path(__file__).parent and item.path.name in DATABASE_TESTS:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def playground_database(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if request.node.path.name not in DATABASE_TESTS:
        return
    from cli_playground.migrate import migrate

    from .helpers import protected

    dsn: str = request.getfixturevalue("postgres_dsn")
    migrate(dsn)
    secret = protected(tmp_path / "postgres-dsn", dsn.encode())
    monkeypatch.setenv("CLI_PLAYGROUND_POSTGRES_DSN_FILE", str(secret))
