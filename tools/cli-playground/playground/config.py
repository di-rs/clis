"""Validated deployment configuration; one gateway worker owns the session limit."""

import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from playground.artifacts import MAX_ARCHIVE, MAX_BINARY


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLAYGROUND_", frozen=True, populate_by_name=True, hide_input_in_errors=True
    )

    data_dir: Path
    public_url: str
    deploy_token: str = Field(min_length=1, repr=False)
    sandbox_image: str = Field(min_length=1, pattern=r"^[^-]")
    sandbox_runtime: Literal["runsc", "runc"] = "runsc"
    preview_domain: str | None = None
    reconcile_sessions_on_start: bool = Field(
        default=False, validation_alias="PLAYGROUND_RECONCILE_SESSIONS"
    )
    cleanup_interval_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_binary_bytes: int = Field(default=MAX_BINARY, gt=0, le=MAX_BINARY)
    max_archive_bytes: int = Field(default=MAX_ARCHIVE, gt=0, le=MAX_ARCHIVE)
    max_sessions: PositiveInt = 8
    session_seconds: float = Field(default=15 * 60, gt=0, allow_inf_nan=False)
    preview_seconds: float = Field(default=7 * 24 * 60 * 60, gt=0, allow_inf_nan=False)

    @field_validator("preview_domain")
    @classmethod
    def optional_domain(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("data_dir")
    @classmethod
    def absolute_mount_path(cls, value: Path) -> Path:
        if not value.is_absolute() or any(c in str(value) for c in ",\n\r"):
            raise ValueError("must be an absolute path without commas/newlines")
        return value

    @field_validator("public_url")
    @classmethod
    def http_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError("must be an HTTP(S) origin without a path")
        _ = parsed.port  # Reject malformed or out-of-range port numbers.
        return value.rstrip("/")

    @model_validator(mode="after")
    def preview_authority(self) -> Self:
        if self.preview_domain is not None:
            parsed = urlsplit(self.public_url)
            if (
                re.fullmatch(
                    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                    self.preview_domain,
                )
                is None
                or len(self.preview_domain) > 220
                or parsed.scheme != "https"
                or parsed.netloc != self.preview_domain
            ):
                raise ValueError("PLAYGROUND_PREVIEW_DOMAIN must match the HTTPS control hostname")
        return self

    def preview_url(self, identifier: str) -> str:
        origin = (
            f"https://{identifier}.{self.preview_domain}"
            if self.preview_domain
            else self.public_url
        )
        return f"{origin}/p/{identifier}"

    def accepts_host(self, host: str, identifier: str | None = None) -> bool:
        if host == urlsplit(self.public_url).netloc:
            return True
        if self.preview_domain is None:
            return False
        match = re.fullmatch(r"([0-9a-f]{32})\." + re.escape(self.preview_domain), host)
        return match is not None and (identifier is None or match[1] == identifier)
