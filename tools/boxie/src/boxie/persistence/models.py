"""Mapped lifecycle records; PostgreSQL constraints protect shared ownership."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    UniqueConstraint,
    and_,
    column,
    false,
    func,
    literal,
    or_,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint(column("digest").regexp_match("^[a-f0-9]{64}$"), name="artifact_digest"),
    )

    id: UUID = Field(primary_key=True)
    consumer_id: str
    digest: str
    storage_id: UUID = Field(unique=True)
    retired: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    collection_pending: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    collected_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default=None,
        nullable=False,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.clock_timestamp()},
    )


class Sandbox(SQLModel, table=True):
    __tablename__ = "sandboxes"
    __table_args__ = (
        UniqueConstraint("consumer_id", "idempotency_key"),
        CheckConstraint(column("generation") > 0, name="sandbox_generation"),
        CheckConstraint(
            column("state").in_(
                ["accepted", "provisioning", "ready", "terminating", "terminated", "failed"]
            ),
            name="sandbox_state",
        ),
        CheckConstraint(
            and_(
                column("absolute_deadline") > column("accepted_at"),
                column("absolute_deadline") <= column("accepted_at") + timedelta(hours=1),
            ),
            name="sandbox_deadline",
        ),
        CheckConstraint(
            or_(
                column("cleaned_at").is_(None),
                and_(~column("cleanup_pending"), column("state").in_(["terminated", "failed"])),
            ),
            name="sandbox_cleaned",
        ),
    )

    id: UUID = Field(primary_key=True)
    consumer_id: str
    idempotency_key: str
    request_hash: str
    spec: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    snapshot: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    installation_id: UUID
    host_id: str
    generation: int = Field(
        default=1, sa_type=BigInteger, sa_column_kwargs={"server_default": literal(1)}
    )
    orchestrator_boot_id: UUID | None = None
    accepted_at: datetime = Field(sa_type=DateTime(timezone=True))
    absolute_deadline: datetime = Field(sa_type=DateTime(timezone=True))
    desired_stop: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    reason: str | None = None
    state: str = Field(default="accepted", sa_column_kwargs={"server_default": literal("accepted")})
    cleanup_pending: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    cleaned_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class CapacitySlot(SQLModel, table=True):
    __tablename__ = "capacity_slots"
    __table_args__ = (CheckConstraint(column("slot").between(1, 8), name="capacity_range"),)

    slot: int = Field(primary_key=True)
    sandbox_id: UUID | None = Field(
        default=None, foreign_key="sandboxes.id", ondelete="RESTRICT", unique=True
    )


class SandboxArtifactRef(SQLModel, table=True):
    __tablename__ = "sandbox_artifact_refs"
    __table_args__ = (Index("artifact_references", "artifact_id"),)

    sandbox_id: UUID = Field(primary_key=True, foreign_key="sandboxes.id", ondelete="RESTRICT")
    artifact_id: UUID = Field(foreign_key="artifacts.id", ondelete="RESTRICT")


class Outbox(SQLModel, table=True):
    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint(column("kind").in_(["start", "stop"]), name="outbox_kind"),
        Index("outbox_pending", "created_at", postgresql_where=column("delivered_at").is_(None)),
    )

    id: UUID = Field(primary_key=True)
    deduplication_key: str = Field(unique=True)
    sandbox_id: UUID = Field(foreign_key="sandboxes.id", ondelete="RESTRICT")
    kind: str
    created_at: datetime = Field(
        default=None,
        nullable=False,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.clock_timestamp()},
    )
    delivered_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class OrchestratorObservationRecord(SQLModel, table=True):
    __tablename__ = "orchestrator_observations"
    __table_args__ = (
        CheckConstraint(column("event_version") > 0, name="observation_version"),
        CheckConstraint(
            or_(
                ~column("removal_confirmed"),
                and_(~column("resource_present"), ~column("cleanup_pending")),
            ),
            name="observation_removal",
        ),
    )

    sandbox_id: UUID = Field(primary_key=True, foreign_key="sandboxes.id", ondelete="RESTRICT")
    generation: int = Field(sa_type=BigInteger)
    boot_id: UUID
    event_version: int = Field(sa_type=BigInteger)
    observed_at: datetime = Field(sa_type=DateTime(timezone=True))
    ready_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    last_input_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    disconnected_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    resource_present: bool
    removal_confirmed: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    cleanup_pending: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    attachment_epoch: int = Field(
        default=0, sa_type=BigInteger, sa_column_kwargs={"server_default": literal(0)}
    )
    absolute_deadline: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    control_lease_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    container_id: str | None = None
