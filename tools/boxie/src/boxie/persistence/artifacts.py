"""Immutable service maxima for generic artifact uploads."""

from pydantic import BaseModel, ConfigDict, Field


class ArtifactLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    compressed_bytes: int = Field(default=512 * 1024 * 1024, gt=0, le=512 * 1024 * 1024)
    expanded_bytes: int = Field(default=512 * 1024 * 1024, gt=0, le=512 * 1024 * 1024)
    member_bytes: int = Field(default=64 * 1024 * 1024, gt=0, le=64 * 1024 * 1024)
    files: int = Field(default=1024, gt=0, le=1024)
