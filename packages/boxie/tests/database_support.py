"""Real SQL setup, accounting assertions and server-enforced fault injection."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from boxie.bootstrap.config import OrchestratorConfig
from boxie.domain.models import Principal, SandboxSpec
from boxie.persistence.database import SessionFactory
from boxie.persistence.database import database as session_database
from boxie.persistence.migrate import migrate
from boxie.persistence.models import CapacitySlot, Outbox, Sandbox, SandboxArtifactRef
from boxie.persistence.postgres import PostgresLifecycle
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie.sandbox.transport.client import OrchestratorClient
from boxie_test_support.orchestrator import bundle, configuration
from boxie_test_support.terminal_network import network
from sqlalchemy import CheckConstraint, func
from sqlalchemy.schema import AddConstraint
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select

MIGRATIONS = Path(__file__).parents[1] / "alembic"
OWNER = Principal("one")


async def add_check(
    sessions: SessionFactory,
    condition: ColumnElement[bool],
    name: str,
    *,
    not_valid: bool = False,
) -> None:
    """Inject a server-enforced failure without changing the shared mapped schema."""
    constraint = CheckConstraint(condition, name=name, postgresql_not_valid=not_valid)
    try:
        async with sessions.begin() as session:
            connection = await session.connection()
            await connection.execute(AddConstraint(constraint))
    finally:
        constraint.table.constraints.discard(constraint)


@asynccontextmanager
async def database(
    dsn: str, root: Path
) -> AsyncIterator[
    tuple[PostgresLifecycle, PostgresArtifactStore, SessionFactory, OrchestratorConfig]
]:
    await asyncio.to_thread(migrate, dsn, MIGRATIONS)
    config = configuration(root)
    async with network(root / "resolver") as harness:
        async with OrchestratorClient(
            harness.orchestrator_url, b"orchestrator-test-secret"
        ) as resolver:
            async with session_database(dsn, pool_size=24) as sessions:

                async def healthy() -> bool:
                    return True

                lifecycle = PostgresLifecycle(sessions, resolver, config.identity, healthy)
                artifacts = PostgresArtifactStore(sessions, root / "artifacts")
                yield lifecycle, artifacts, sessions, config


async def upload(artifacts: PostgresArtifactStore) -> SandboxSpec:
    content = bundle()

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    result = await artifacts.put(OWNER, uuid4(), hashlib.sha256(content).hexdigest(), chunks())
    return SandboxSpec(result.artifact.id, "shell", ("/bin/sh",), {})


async def counts(sessions: SessionFactory) -> tuple[int, int, int, int]:
    """Admission/slot/reference/command counts; observation notifications tested separately."""
    async with sessions() as session:
        sandboxes = await session.scalar(select(func.count()).select_from(Sandbox))
        slots = await session.scalar(
            select(func.count())
            .select_from(CapacitySlot)
            .where(col(CapacitySlot.sandbox_id).is_not(None))
        )
        references = await session.scalar(select(func.count()).select_from(SandboxArtifactRef))
        commands = await session.scalar(
            select(func.count()).select_from(Outbox).where(col(Outbox.kind).in_(("start", "stop")))
        )
        assert (
            sandboxes is not None
            and slots is not None
            and references is not None
            and commands is not None
        )
        return sandboxes, slots, references, commands
