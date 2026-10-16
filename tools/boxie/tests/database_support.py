"""Database-level fault injection expressed through SQLAlchemy schema objects."""

from boxie.persistence.database import SessionFactory
from sqlalchemy import CheckConstraint
from sqlalchemy.schema import AddConstraint
from sqlalchemy.sql.elements import ColumnElement


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
