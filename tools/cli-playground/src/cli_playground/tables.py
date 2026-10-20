"""Playground-owned SQLModel tables. Remote sandbox contracts stay snapshots."""

from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    MetaData,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Table(SQLModel):
    metadata: ClassVar[MetaData] = MetaData()


class Preview(Table, table=True):
    __tablename__ = "preview"
    __table_args__ = (
        UniqueConstraint("id"),
        CheckConstraint("pr > 0", name="positive_pr"),
        CheckConstraint(
            "num_nonnulls(build_state, build_sha, run_id, run_number, run_attempt) = 0 OR (num_nonnulls(build_state, build_sha, run_id, run_number, run_attempt) = 5 AND build_state IN ('building','failed','empty','cancelled','ready') AND run_id > 0 AND run_number > 0 AND run_attempt > 0)",
            name="valid_build",
        ),
        ForeignKeyConstraint(
            ["pr", "current_revision"],
            ["artifact_revision.pr", "artifact_revision.revision"],
            name="preview_current_revision",
            use_alter=True,
        ),
    )
    pr: int = Field(primary_key=True, sa_type=BigInteger)
    id: str
    closed: bool = False
    expires_at: float | None = None
    build_sha: str | None = None
    run_id: int | None = Field(default=None, sa_type=BigInteger)
    run_number: int | None = Field(default=None, sa_type=BigInteger)
    run_attempt: int | None = Field(default=None, sa_type=BigInteger)
    build_state: str | None = None
    current_revision: str | None = None


class Revision(Table, table=True):
    __tablename__ = "artifact_revision"
    pr: int = Field(primary_key=True, foreign_key="preview.pr", sa_type=BigInteger)
    revision: str = Field(primary_key=True)
    artifact_id: UUID = Field(unique=True)
    sha: str
    sha256: str
    clis: list[str] = Field(sa_column=Column(JSONB, nullable=False))
    expires_at: float


class Binding(Table, table=True):
    __tablename__ = "session_binding"
    id: UUID = Field(primary_key=True)
    preview_id: str = Field(index=True, foreign_key="preview.id")
    revision: str
    credential_hash: str
    sandbox: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    pending_stop: bool = Field(default=False, index=True)
    attachment_deadline: float | None = None
    attached: bool = False
    attachment_claim: int = Field(default=0, sa_type=BigInteger)


class Creation(Table, table=True):
    __tablename__ = "create_intent"
    id: UUID = Field(primary_key=True)
    preview_id: str = Field(foreign_key="preview.id")
    revision: str
    credential_hash: str
    spec: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    claim_until: float
    abandoned: bool = False


class Upload(Table, table=True):
    __tablename__ = "upload_intent"
    __table_args__ = (UniqueConstraint("pr", "revision"),)
    artifact_id: UUID = Field(primary_key=True)
    pr: int = Field(index=True, foreign_key="preview.pr", sa_type=BigInteger)
    revision: str
    sha: str
    sha256: str
    clis: list[str] = Field(sa_column=Column(JSONB, nullable=False))
    expires_at: float
    build_sha: str | None = None
    run_id: int | None = Field(default=None, sa_type=BigInteger)
    run_number: int | None = Field(default=None, sa_type=BigInteger)
    run_attempt: int | None = Field(default=None, sa_type=BigInteger)
    previous_revision: str | None = None
    cancelled: bool = False


class Retirement(Table, table=True):
    __tablename__ = "artifact_retirement"
    artifact_id: UUID = Field(primary_key=True)
    completed: bool = False
