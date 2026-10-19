"""Real dependencies are required when integration tests are explicitly selected."""

import secrets
import subprocess
import time
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

POSTGRES_IMAGE = (
    "postgres:17.11@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3"
)


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[str]:
    name = "boxie-pg-test-" + uuid4().hex[:12]
    password = secrets.token_hex(24)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_PASSWORD=" + password,
            POSTGRES_IMAGE,
        )
        port = docker("port", name, "5432/tcp").rsplit(":", 1)[1]
        dsn = f"host=127.0.0.1 port={port} dbname=postgres user=postgres password={password}"
        for _ in range(120):
            try:
                with psycopg.connect(dsn, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                time.sleep(0.25)
        else:
            pytest.fail("isolated PostgreSQL did not become ready: " + docker("logs", name))
        yield dsn
    finally:
        docker("rm", "--force", "--volumes", name)


@pytest.fixture
def postgres_role(postgres_server: str) -> Iterator[tuple[str, str]]:
    # Separate acquisitions ensure a failed database setup still releases its role.
    name = "boxie_" + uuid4().hex
    password = secrets.token_hex(24)
    with psycopg.connect(postgres_server, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
    try:
        yield name, password
    finally:
        with psycopg.connect(postgres_server, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


@pytest.fixture
def postgres_dsn(postgres_server: str, postgres_role: tuple[str, str]) -> Iterator[str]:
    # Each test owns a database and a non-superuser role, including concurrent tests.
    # These administration-only statements provision the database itself; application
    # tables are accessed exclusively through SQLModel sessions in the tests.
    name, password = postgres_role
    with psycopg.connect(postgres_server, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(name)
            )
        )
    try:
        with psycopg.connect(postgres_server, autocommit=True) as conn:
            conn.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
        info = psycopg.conninfo.conninfo_to_dict(postgres_server)
        info.update(dbname=name, user=name, password=password)
        yield psycopg.conninfo.make_conninfo("", **info)
    finally:
        with psycopg.connect(postgres_server, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def temporal_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[object]:
    from run_integration import ProductionStack

    stack = ProductionStack(tmp_path_factory.mktemp("boxie-production"))
    try:
        stack.start()
        yield stack
    finally:
        stack.close()
