"""Shared isolated component source fixtures."""

import json
from pathlib import Path

import pytest

DIGEST = "registry.example/test@sha256:" + "a" * 64


@pytest.fixture
def source(tmp_path: Path) -> Path:
    (tmp_path / "compose.yml").write_text(
        "services:\n  web:\n    image: ${APP_IMAGE}\n"
        "    networks: [private]\nnetworks:\n  private:\n    external: true\n"
    )
    (tmp_path / "descriptor.json").write_text(
        json.dumps(
            {
                "component": "sample",
                "compose_file": "compose.yml",
                "files": [],
                "services": ["web"],
                "image_variables": {"APP_IMAGE": ["web"]},
                "required_environment": ["TOKEN"],
                "healthchecks": {"web": {"kind": "docker"}},
            }
        )
    )
    (tmp_path / "release.json").write_text(
        json.dumps(
            {
                "component": "sample",
                "revision": "1" * 40,
                "images": {"APP_IMAGE": DIGEST},
                "compatibility": {"schema": "sample-v1", "dependency": "persistence-v1"},
            }
        )
    )
    (tmp_path / "env.json").write_text(json.dumps({"TOKEN": "never-log-me"}))
    return tmp_path
