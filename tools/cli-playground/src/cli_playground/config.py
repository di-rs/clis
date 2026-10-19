"""Validated deployment configuration; one backend worker owns the session limit."""

import hashlib
import re
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cli_playground.artifacts import MAX_ARCHIVE, MAX_BINARY


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLI_PLAYGROUND_", frozen=True, populate_by_name=True, hide_input_in_errors=True
    )

    data_dir: Path
    public_url: str
    deploy_token: str = Field(min_length=1, repr=False)
    boxie_url: str
    boxie_credential_file: Path
    boxie_profile: str = "cli-playground"
    preview_domain: str | None = None
    cleanup_interval_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_binary_bytes: int = Field(default=MAX_BINARY, gt=0, le=MAX_BINARY)
    max_archive_bytes: int = Field(default=MAX_ARCHIVE, gt=0, le=MAX_ARCHIVE)
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

    @field_validator("public_url", "boxie_url")
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
                raise ValueError(
                    "CLI_PLAYGROUND_PREVIEW_DOMAIN must match the HTTPS control hostname"
                )
        return self

    def preview_url(self, identifier: str, pr: int | None = None) -> str:
        label = f"pr-{pr}"
        origin = (
            f"https://{label}.{self.preview_domain}"
            if self.preview_domain and pr is not None
            else self.public_url
        )
        return f"{origin}/p/{identifier}"

    def accepts_host(self, host: str, identifier: str | None = None) -> bool:
        if host == urlsplit(self.public_url).netloc:
            return True
        if self.preview_domain is None:
            return False
        pr_match = re.fullmatch(r"pr-([1-9][0-9]*)\." + re.escape(self.preview_domain), host)
        if pr_match is not None:
            expected = hashlib.sha256(f"pull-request:{pr_match[1]}".encode()).hexdigest()[:32]
            return identifier is None or identifier == expected
        return False
