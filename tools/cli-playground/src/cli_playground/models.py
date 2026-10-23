"""Validated artifact metadata and the public preview/terminal protocol."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    field_validator,
)

from cli_playground.artifacts import CLI_PATTERN, MAX_CLIS
from cli_playground.catalog import Catalog

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
    catalog: Catalog | None = None
    catalog_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None

    clis: Annotated[list[CliName], Field(min_length=1, max_length=MAX_CLIS)]
    expires_at: FiniteFloat
    revision: ArtifactRevision
    artifact_id: UUID
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("clis")
    @classmethod
    def unique_clis(cls, names: list[str]) -> list[str]:
        if len(set(names)) != len(names):
            raise ValueError("CLI names must be unique")
        return sorted(names)


class Environment(ArtifactMetadata):
    id: PreviewId

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
