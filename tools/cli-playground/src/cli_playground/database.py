"""PostgreSQL adapter lifecycle; application ports never expose SQL sessions."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Session
from sqlmodel.ext.asyncio.session import AsyncSession

from cli_playground.ports import PersistenceUnavailable, StoreRunner

SessionFactory = async_sessionmaker[AsyncSession]


def session_factory(url: str) -> SessionFactory:
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=10000 -c statement_timeout=30000",
        },
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def check_schema(sessions: SessionFactory) -> None:
    async with sessions() as session:
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if version != "0001_metadata":
            raise RuntimeError("Playground database needs an explicit schema upgrade")


def adapt_store[Store](
    sessions: SessionFactory, factory: Callable[[Session], Store]
) -> StoreRunner[Store]:
    async def run[Result](operation: Callable[[Store], Result]) -> Result:
        async with sessions() as session:
            return await session.run_sync(lambda sync: operation(factory(cast(Session, sync))))

    return run


@contextmanager
def connection(session: Session, *, write: bool = False) -> Iterator[Session]:
    try:
        with session:
            if write:
                with session.begin():
                    yield session
            else:
                yield session
    except SQLAlchemyError as error:
        raise PersistenceUnavailable("playground database unavailable") from error
