"""PostgreSQL adapter lifecycle; application ports never expose SQL sessions."""

import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import Error as PsycopgError
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

SessionFactory = async_sessionmaker[AsyncSession]


def database_url(dsn: str) -> URL:
    try:
        parameters = {
            key: str(value) for key, value in conninfo_to_dict(dsn).items() if value is not None
        }
    except PsycopgError, ValueError:
        raise ValueError("invalid PostgreSQL DSN") from None
    return URL.create(
        "postgresql+psycopg",
        username=parameters.pop("user", None),
        password=parameters.pop("password", None),
        database=parameters.pop("dbname", None),
        query=parameters,
    )


def read_dsn(path: Path) -> str:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "r") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("PostgreSQL DSN must be a private regular file")
        dsn = stream.read(4097).strip()
    if not dsn or len(dsn) > 4096:
        raise ValueError("invalid PostgreSQL DSN file")
    return dsn


def session_factory(dsn: str) -> SessionFactory:
    url = database_url(dsn)
    options = (
        str(url.query.get("options", "")) + " -c lock_timeout=10000 -c statement_timeout=30000"
    )
    engine = create_async_engine(
        url.update_query_dict({"options": options}),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def database(dsn: str) -> AsyncIterator[SessionFactory]:
    sessions = session_factory(dsn)
    try:
        yield sessions
    finally:
        await sessions.kw["bind"].dispose()


async def check_schema(sessions: SessionFactory) -> None:
    async with sessions() as session:
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if version != "0001_metadata":
            raise RuntimeError("Playground database needs an explicit schema upgrade")


@asynccontextmanager
async def connection(
    sessions: SessionFactory, *, write: bool = False
) -> AsyncIterator[AsyncSession]:
    from sqlalchemy.exc import SQLAlchemyError

    from cli_playground.ports import PersistenceUnavailable

    try:
        async with sessions() as session:
            if write:
                async with session.begin():
                    yield session
            else:
                yield session
    except SQLAlchemyError as error:
        raise PersistenceUnavailable("playground database unavailable") from error
