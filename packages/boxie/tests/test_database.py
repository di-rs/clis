"""Database secret formats retain their meaning through Alembic URL serialization."""

import pytest
from boxie.persistence.database import database_url
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://one:p%25%40ss@localhost:5433/boxie?sslmode=require",
        "host=/var/run/postgresql dbname=boxie user=one password='p ss%word'",
        "host=first,second port=5432,5433 dbname=boxie user=one connect_timeout=10",
    ],
)
def test_database_secret_survives_url_round_trip(dsn: str) -> None:
    expected = conninfo_to_dict(dsn)
    url = make_url(database_url(dsn).render_as_string(hide_password=False))
    engine = create_engine(url)
    args, actual = engine.dialect.create_connect_args(url)
    engine.dispose()
    assert args == []
    for key, value in expected.items():
        assert str(actual[key]) == str(value)
