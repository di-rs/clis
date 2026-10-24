"""Validated, bounded build-size artifacts shared by collection and reporting."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Build(Record):
    status: Literal["complete", "failed", "absent"]
    bytes: int | None = Field(default=None, ge=0, le=10**12)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "complete") != (self.bytes is not None):
            raise ValueError("only complete builds have byte sizes")
        return self


class BinarySize(Record):
    package: Name
    binary: Name
    base: Build
    head: Build

    @model_validator(mode="after")
    def present(self) -> Self:
        if self.base.status == self.head.status == "absent":
            raise ValueError("binary absent from both revisions")
        return self


class SizeReport(Record):
    schema_version: Literal[1] = 1
    base_sha: Sha
    head_sha: Sha
    run_attempt: int = Field(ge=1)
    toolchain: Name
    target: Name
    profile: Literal["release"] = "release"
    features: Literal["default"] = "default"
    rows: list[BinarySize] = Field(max_length=256)

    @model_validator(mode="after")
    def unique(self) -> Self:
        keys = {(row.package, row.binary) for row in self.rows}
        if len(keys) != len(self.rows):
            raise ValueError("duplicate binary")
        return self
