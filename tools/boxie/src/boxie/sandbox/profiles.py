"""Operator-controlled profiles, atomically reloaded and retained by configuration hash."""

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from pydantic import Field, ValidationError, field_validator, model_validator

from boxie.domain.errors import BoxieError
from boxie.domain.models import ProfileSnapshot
from boxie.domain.policy import validate_limits
from boxie.persistence.atomic import atomic_write
from boxie.sandbox.protocol import LimitsWire, WireModel


class ProfileConfig(WireModel):
    image_digest: str
    commands: tuple[tuple[str, ...], ...]
    env: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    artifact_target: str = "/opt/artifact"
    limits: LimitsWire = Field(default_factory=LimitsWire)

    @field_validator("image_digest")
    @classmethod
    def pinned_image(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@)?sha256:[a-f0-9]{64}", value):
            raise ValueError("image must be pinned by digest")
        return value

    @field_validator("artifact_target")
    @classmethod
    def safe_target(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value.startswith("/opt/") or ".." in path.parts or str(path) != value:
            raise ValueError("artifact target must be a normalized path under /opt")
        return value

    @model_validator(mode="after")
    def approved_values(self) -> ProfileConfig:
        if not self.commands or len(self.commands) > 128:
            raise ValueError("profile requires approved commands")
        for command in self.commands:
            if not command or len(command) > 64 or not command[0].startswith("/"):
                raise ValueError("command must use an absolute executable")
            if any(not value or "\x00" in value or len(value) > 4096 for value in command):
                raise ValueError("invalid command argument")
        if len(self.env) > 128:
            raise ValueError("too many environment keys")
        for key, values in self.env.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or not values:
                raise ValueError("invalid environment allowlist")
            if any("\x00" in value or len(value) > 4096 for value in values):
                raise ValueError("invalid environment value")
        validate_limits(self.limits.domain())
        return self


class ProfileRegistry:
    def __init__(self, source: Path, versions: Path) -> None:
        self.source, self.versions = source, versions
        versions.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._approved: dict[str, dict[str, ProfileConfig]] = {}
        for path in versions.glob("*.json"):
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != path.stem:
                raise BoxieError(422, "profile version integrity failure")
            self._approved[path.stem] = self._parse(content)
        self.current = ""
        self.reload()

    @staticmethod
    def _parse(content: bytes) -> dict[str, ProfileConfig]:
        try:
            data = json.loads(content)
            if not isinstance(data, dict) or not data or len(data) > 128:
                raise ValueError("profile file must contain 1..128 profiles")
            result = {}
            for name, values in data.items():
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
                    raise ValueError("invalid profile ID")
                result[name] = ProfileConfig.model_validate(values)
            return result
        except (ValueError, ValidationError) as error:
            raise BoxieError(422, "invalid trusted profile configuration") from error

    def reload(self) -> None:
        content = self.source.read_bytes()
        if len(content) > 1024 * 1024:
            raise BoxieError(422, "profile configuration too large")
        profiles = self._parse(content)
        digest = hashlib.sha256(content).hexdigest()
        atomic_write(self.versions / f"{digest}.json", content)
        self._approved[digest] = profiles
        self.current = digest

    def resolve_profile(
        self, profile_id: str, command: tuple[str, ...], env: Mapping[str, str]
    ) -> ProfileSnapshot:
        return self._resolve(self.current, profile_id, command, env)

    def _resolve(
        self, digest: str, profile_id: str, command: tuple[str, ...], env: Mapping[str, str]
    ) -> ProfileSnapshot:
        profile = self._approved.get(digest, {}).get(profile_id)
        if profile is None or command not in profile.commands:
            raise BoxieError(422, "unapproved profile or command")
        if any(value not in profile.env.get(key, ()) for key, value in env.items()):
            raise BoxieError(422, "unapproved environment")
        return ProfileSnapshot(
            profile_id,
            digest,
            profile.image_digest,
            command,
            env,
            profile.artifact_target,
            profile.limits.domain(),
        )

    def validate_snapshot(self, snapshot: ProfileSnapshot) -> ProfileSnapshot:
        approved = self._resolve(
            snapshot.config_digest, snapshot.profile_id, snapshot.command, snapshot.env
        )
        if snapshot != approved:
            raise BoxieError(422, "snapshot differs from approved configuration")
        return approved
