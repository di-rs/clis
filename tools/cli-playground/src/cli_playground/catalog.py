"""Bounded, versioned descriptions from untrusted CLI executables."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_CATALOG = 4 * 1024 * 1024
CATALOG_NAME = "catalog.json"
Token = Annotated[str, StringConstraints(pattern=r"^[^\x00-\x20\x7f-\x9f]{1,128}$")]
Text = Annotated[
    str, StringConstraints(max_length=8192, pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]*$")
]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Sha = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{40}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class Arity(Model):
    min: Annotated[int, Field(ge=0, le=65536)]
    max: Annotated[int, Field(ge=0, le=65536)] | None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.max is not None and self.max < self.min:
            raise ValueError("invalid value count")
        return self


class Argument(Model):
    id: Token
    short: (
        Annotated[
            str, StringConstraints(min_length=1, max_length=1, pattern=r"^[^\x00-\x20\x7f-\x9f]$")
        ]
        | None
    )
    long: Token | None
    aliases: Annotated[list[Token], Field(max_length=128)]
    help: Text
    long_help: Text | None
    value_names: Annotated[list[Text], Field(max_length=128)]
    default_values: Annotated[list[Text], Field(max_length=128)]
    possible_values: Annotated[list[Text], Field(max_length=1024)]
    required: bool
    index: Annotated[int, Field(gt=0, le=65536)] | None
    num_args: Arity
    action: Literal["set", "append", "set_true", "set_false", "count", "help", "version", "unknown"]
    global_: bool = Field(alias="global")
    owner: Annotated[list[Token], Field(max_length=16)]


class Command(Model):
    name: Token
    aliases: Annotated[list[Token], Field(max_length=128)]
    about: Text
    long_about: Text | None
    usage: Text
    usage_override: Text | None
    args: Annotated[list[Argument], Field(max_length=1024)]
    subcommands: Annotated[list[Command], Field(max_length=256)]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({arg.id for arg in self.args}) != len(self.args):
            raise ValueError("duplicate argument")
        if len({child.name for child in self.subcommands}) != len(self.subcommands):
            raise ValueError("duplicate command")
        return self


class CliSchema(Model):
    schema_version: Literal[1]
    command: Command


class CliCatalog(Model):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    binary_sha256: Digest
    head: CliSchema
    base: CliSchema | None
    comparison: Literal["available", "added", "unavailable"]

    @model_validator(mode="after")
    def identities(self) -> Self:
        if self.head.command.name != self.name or self.base and self.base.command.name != self.name:
            raise ValueError("catalog CLI identity mismatch")
        if (self.base is not None) != (self.comparison == "available"):
            raise ValueError("catalog comparison mismatch")
        return self


class Catalog(Model):
    schema_version: Literal[1]
    head_sha: Sha
    base_sha: Sha
    clis: Annotated[list[CliCatalog], Field(min_length=1, max_length=1024)]

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len({cli.name for cli in self.clis}) != len(self.clis):
            raise ValueError("duplicate CLI")
        pending = [
            (schema.command, 0) for cli in self.clis for schema in (cli.head, cli.base) if schema
        ]
        commands = arguments = 0
        while pending:
            command, depth = pending.pop()
            commands += 1
            arguments += len(command.args)
            if depth > 16 or commands > 4096 or arguments > 32768:
                raise ValueError("catalog tree exceeds limits")
            pending.extend((child, depth + 1) for child in command.subcommands)
        return self

    def canonical(self) -> bytes:
        return json.dumps(
            self.model_dump(by_alias=True), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()

    def digest(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()


def parse_catalog(data: bytes, sha: str, binaries: dict[str, str]) -> Catalog:
    if len(data) > MAX_CATALOG:
        raise ValueError("catalog exceeds size limit")
    catalog = Catalog.model_validate_json(data)
    if catalog.head_sha != sha or {cli.name: cli.binary_sha256 for cli in catalog.clis} != binaries:
        raise ValueError("catalog does not match published binaries")
    if len(catalog.canonical()) > MAX_CATALOG:
        raise ValueError("canonical catalog exceeds size limit")
    return catalog
