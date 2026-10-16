"""Orchestrator configuration comes from operator-owned files, never from API consumers."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from boxie.domain.models import LifetimePolicy, OrchestratorIdentity
from boxie.domain.policy import validate_policy


def read_credential(path: Path, *, allow_spaces: bool = False) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("credential must be a private regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            value = source.read(4097).strip()
        if not 16 <= len(value) <= 4096 or any(
            byte < (32 if allow_spaces else 33) or byte > 126 for byte in value
        ):
            raise ValueError("invalid credential")
        return value
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class OrchestratorConfig:
    data_dir: Path
    profiles_file: Path
    credential_file: Path
    identity: OrchestratorIdentity
    policy_maximum: LifetimePolicy = field(default_factory=LifetimePolicy)
    docker_timeout: float = 20
    sweep_seconds: float = 1
    bind_host: str = "127.0.0.1"
    bind_port: int = 8091
    postgres_dsn_file: Path | None = None
    lifecycle: LifecycleConfig | None = None

    def __post_init__(self) -> None:
        if not 0 < self.docker_timeout <= 60 or not 0 < self.sweep_seconds <= 10:
            raise ValueError("invalid orchestrator intervals")
        validate_policy(self.policy_maximum)
        if not self.identity.host_id or len(self.identity.host_id) > 128:
            raise ValueError("host ID required")

    @classmethod
    def from_file(cls, path: Path) -> OrchestratorConfig:
        data = json.loads(path.read_text())
        for key in ("data_dir", "profiles_file", "credential_file"):
            data[key] = Path(data[key])
        if "postgres_dsn_file" in data:
            data["postgres_dsn_file"] = Path(data["postgres_dsn_file"])
        if "lifecycle" in data:
            converted: dict[str, Any] = {
                key: Path(value) if key.endswith("_file") else value
                for key, value in data["lifecycle"].items()
            }
            data["lifecycle"] = LifecycleConfig(**converted)
        identity = data.pop("identity")
        data["identity"] = OrchestratorIdentity(
            UUID(identity["installation_id"]), identity["host_id"]
        )
        if "policy_maximum" in data:
            data["policy_maximum"] = LifetimePolicy(**data["policy_maximum"])
        return cls(**data)


@dataclass(frozen=True)
class LifecycleConfig:
    """Explicit inputs for the coordinated lifecycle composition; no migration hook.

    API and orchestrator use separate Temporal client identities. The orchestrator
    remains the sole Docker and artifact filesystem owner.
    """

    postgres_dsn_file: Path
    temporal_address: str
    temporal_server_name: str
    temporal_ca_file: Path
    temporal_cert_file: Path
    temporal_key_file: Path
    temporal_namespace: str = "boxie"

    def __post_init__(self) -> None:
        if (
            not self.temporal_address
            or not self.temporal_server_name
            or not self.temporal_namespace
        ):
            raise ValueError("Temporal address, verified server name and namespace are required")
