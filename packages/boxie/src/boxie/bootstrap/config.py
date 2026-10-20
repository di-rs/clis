"""Supervisor configuration comes from operator-owned files, never from API consumers."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from boxie.domain.models import LifetimePolicy, SupervisorIdentity
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


def validate_file_model[T: BaseModel](model: type[T], values: dict[str, Any]) -> T:
    """Keep Pydantic's raw input out of errors crossing the file boundary."""
    try:
        return model.model_validate(values)
    except ValidationError as error:
        # hide_input_in_errors only affects formatted text; errors() and json()
        # retain inputs, including secrets already read from credential files.
        locations = []
        for issue in error.errors(include_input=False, include_context=False):
            path = (
                ".".join(
                    segment
                    if isinstance(segment, str) and segment in model.model_fields
                    else "<unknown field>"
                    for segment in issue["loc"]
                )
                or "<model>"
            )
            locations.append(f"{path}: {issue['type']}")
        details = ", ".join(locations)
    raise ValueError(f"invalid configuration: {details}")


class FileOwnedSettings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


class SupervisorConfig(FileOwnedSettings):
    data_dir: Path
    profiles_file: Path
    credential_file: Path
    identity: SupervisorIdentity
    policy_maximum: LifetimePolicy = Field(default_factory=LifetimePolicy)
    docker_timeout: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)
    sweep_seconds: float = Field(default=1, gt=0, le=10, allow_inf_nan=False)
    bind_host: str = "127.0.0.1"
    bind_port: int = 8091
    postgres_dsn_file: Path | None = None
    lifecycle: LifecycleConfig | None = None

    @model_validator(mode="after")
    def valid_domain_values(self) -> Self:
        validate_policy(self.policy_maximum)
        if not self.identity.host_id or len(self.identity.host_id) > 128:
            raise ValueError("host ID required")
        return self

    @classmethod
    def from_file(cls, path: Path) -> SupervisorConfig:
        data = json.loads(path.read_text())
        if "lifecycle" in data:
            data["lifecycle"] = validate_file_model(LifecycleConfig, data["lifecycle"])
        identity = data.pop("identity")
        data["identity"] = SupervisorIdentity(
            UUID(identity["installation_id"]), identity["host_id"]
        )
        if "policy_maximum" in data:
            data["policy_maximum"] = LifetimePolicy(**data["policy_maximum"])
        return validate_file_model(cls, data)


class LifecycleConfig(BaseModel):
    """Explicit inputs for the coordinated lifecycle composition; no migration hook.

    API and supervisor use separate Temporal client identities. The supervisor
    remains the sole Docker and artifact filesystem owner.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    postgres_dsn_file: Path
    temporal_address: str = Field(min_length=1)
    temporal_server_name: str = Field(min_length=1)
    temporal_ca_file: Path
    temporal_cert_file: Path
    temporal_key_file: Path
    temporal_namespace: str = Field(default="boxie", min_length=1)


SupervisorConfig.model_rebuild()
