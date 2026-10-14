"""Validated artifact metadata and the public preview/terminal protocol."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    TypeAdapter,
    field_validator,
)

from playground.artifacts import CLI_PATTERN, MAX_CLIS

SHA_PATTERN = r"[0-9a-f]{40}"
ID_PATTERN = r"[0-9a-f]{32}"

CliName = Annotated[str, StringConstraints(pattern=f"^{CLI_PATTERN}$")]
Revision = Annotated[str, StringConstraints(pattern=f"^{SHA_PATTERN}$")]
PreviewId = Annotated[str, StringConstraints(pattern=f"^{ID_PATTERN}$")]


class ArtifactIdentity(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    sha: Revision
    pr: Annotated[int, Field(gt=0)]


class ArtifactMetadata(ArtifactIdentity):
    clis: Annotated[list[CliName], Field(min_length=1, max_length=MAX_CLIS)]
    expires_at: FiniteFloat

    @field_validator("clis")
    @classmethod
    def unique_clis(cls, names: list[str]) -> list[str]:
        if len(set(names)) != len(names):
            raise ValueError("CLI names must be unique")
        return sorted(names)


class Environment(ArtifactMetadata):
    id: PreviewId
    binary_dir: Path

    def metadata(self) -> EnvironmentMetadata:
        return EnvironmentMetadata(
            id=self.id,
            clis=self.clis,
            sha=self.sha,
            pr=self.pr,
            expires_at=datetime.fromtimestamp(self.expires_at, UTC).isoformat(),
        )


class EnvironmentMetadata(BaseModel):
    id: str
    clis: list[str]
    sha: str
    pr: int
    expires_at: str


class DeploymentResult(BaseModel):
    id: str
    url: str


class TerminalInput(BaseModel):
    model_config = ConfigDict(strict=True)

    type: Literal["input"]
    data: str


class TerminalResize(BaseModel):
    model_config = ConfigDict(strict=True)

    type: Literal["resize"]
    cols: int
    rows: int


terminal_message = TypeAdapter(
    Annotated[TerminalInput | TerminalResize, Field(discriminator="type")]
)
