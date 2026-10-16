"""Deployment configuration requires a verified Temporal server identity."""

from dataclasses import replace
from pathlib import Path

import pytest
from boxie.bootstrap.config import LifecycleConfig


def test_lifecycle_configuration_requires_verified_tls_and_explicit_dsn(tmp_path: Path) -> None:
    settings = LifecycleConfig(
        postgres_dsn_file=tmp_path / "db",
        temporal_address="temporal:7233",
        temporal_server_name="temporal",
        temporal_ca_file=tmp_path / "ca",
        temporal_cert_file=tmp_path / "cert",
        temporal_key_file=tmp_path / "key",
    )
    with pytest.raises(ValueError):
        replace(settings, temporal_address="")
    with pytest.raises(ValueError):
        replace(settings, temporal_server_name="")
    with pytest.raises(ValueError):
        replace(settings, temporal_namespace="")
