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
    model_validator,
)

from playground.artifacts import CLI_PATTERN, MAX_CLIS

SHA_PATTERN = r"[0-9a-f]{40}"
ID_PATTERN = r"[0-9a-f]{32}"
ARTIFACT_REVISION_PATTERN = SHA_PATTERN + r"(?:-[1-9][0-9]*-[1-9][0-9]*)?"

CliName = Annotated[str, StringConstraints(pattern=f"^{CLI_PATTERN}$")]
Revision = Annotated[str, StringConstraints(pattern=f"^{SHA_PATTERN}$")]
PreviewId = Annotated[str, StringConstraints(pattern=f"^{ID_PATTERN}$")]
ArtifactRevision = Annotated[str, StringConstraints(pattern=f"^{ARTIFACT_REVISION_PATTERN}$")]


class BuildIdentity(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    sha: Revision
    run_id: Annotated[int, Field(gt=0)]
    run_number: Annotated[int, Field(gt=0)]
    run_attempt: Annotated[int, Field(gt=0)]

    @property
    def order(self) -> tuple[int, int]:
        return self.run_number, self.run_attempt


class BuildUpdate(BuildIdentity):
    state: Literal["building", "failed", "empty", "cancelled"]


class BuildStatus(BuildIdentity):
    state: Literal["building", "failed", "empty", "cancelled", "ready"]


class ArtifactIdentity(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    sha: Revision
    pr: Annotated[int, Field(gt=0)]


class ArtifactMetadata(ArtifactIdentity):
    clis: Annotated[list[CliName], Field(min_length=1, max_length=MAX_CLIS)]
    expires_at: FiniteFloat
    revision: ArtifactRevision | None = None

    @model_validator(mode="after")
    def legacy_revision(self) -> ArtifactMetadata:
        if self.revision is None:
            object.__setattr__(self, "revision", self.sha)
        return self

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
            revision=self.revision,
        )


class PreviewRecord(BaseModel):
    pr: Annotated[int, Field(gt=0)]
    expires_at: FiniteFloat
    build: BuildStatus | None = None
    artifact: ArtifactMetadata | None = None


class EnvironmentMetadata(BaseModel):
    id: str
    clis: list[str]
    sha: str | None
    pr: int
    expires_at: str
    revision: str | None = None
    build: BuildStatus | None = None


class DeploymentResult(BaseModel):
    id: str
    url: str


class BuildUpdateResult(EnvironmentMetadata):
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
