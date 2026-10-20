"""Deployment configuration requires a verified Temporal server identity."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from boxie.bootstrap.config import LifecycleConfig, SupervisorConfig
from pydantic import ValidationError


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
        LifecycleConfig.model_validate({**settings.model_dump(), "temporal_address": ""})
    with pytest.raises(ValueError):
        LifecycleConfig.model_validate({**settings.model_dump(), "temporal_server_name": ""})
    with pytest.raises(ValueError):
        LifecycleConfig.model_validate({**settings.model_dump(), "temporal_namespace": ""})


def test_supervisor_file_ignores_environment_and_rejects_unknown_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "supervisor.json"
    data = {
        "data_dir": str(tmp_path / "approved"),
        "profiles_file": str(tmp_path / "profiles.json"),
        "credential_file": str(tmp_path / "credential"),
        "identity": {"installation_id": str(uuid4()), "host_id": "host-one"},
    }
    config_path.write_text(json.dumps(data))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "unapproved"))
    monkeypatch.setenv("BOXIE_DATA_DIR", str(tmp_path / "unapproved"))
    assert SupervisorConfig.from_file(config_path).data_dir == tmp_path / "approved"

    config_path.write_text(json.dumps({**data, "secret-value": "secret-value"}))
    with pytest.raises(ValueError) as raised:
        SupervisorConfig.from_file(config_path)
    assert "secret-value" not in str(raised.value)
    assert "secret-value" not in repr(raised.value.args)
    assert raised.value.__context__ is None


def test_lifecycle_model_validation_rejects_invalid_intervals(tmp_path: Path) -> None:
    values = {
        "postgres_dsn_file": tmp_path / "db",
        "temporal_address": "temporal:7233",
        "temporal_server_name": "temporal",
        "temporal_ca_file": tmp_path / "ca",
        "temporal_cert_file": tmp_path / "cert",
        "temporal_key_file": tmp_path / "key",
    }
    with pytest.raises(ValidationError):
        LifecycleConfig.model_validate({**values, "temporal_namespace": ""})
