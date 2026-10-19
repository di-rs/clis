"""Standard SQLAlchemy engines and SQLModel sessions for one transaction per task."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

SessionFactory = async_sessionmaker[AsyncSession]


def database_url(dsn: str) -> URL:
    """Accept the existing PostgreSQL URI and libpq secret-file formats."""
    parameters = {
        key: str(value) for key, value in conninfo_to_dict(dsn).items() if value is not None
    }
    return URL.create(
        "postgresql+psycopg",
        username=parameters.pop("user", None),
        password=parameters.pop("password", None),
        database=parameters.pop("dbname", None),
        query=parameters,
    )


@asynccontextmanager
async def database(
    dsn: str, *, pool_size: int = 16, max_overflow: int = 0, pool_timeout: float = 5
) -> AsyncIterator[SessionFactory]:
    url = database_url(dsn)
    # Driver connection options bound row/advisory-lock waits without SQL strings.
    options = str(url.query.get("options", "")) + " -c lock_timeout=10000"
    engine = create_async_engine(
        url.update_query_dict({"options": options}),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()
