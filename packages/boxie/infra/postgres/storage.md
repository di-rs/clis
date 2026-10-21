# Boxie schema and retention

Application tables are mapped SQLModel records. Each operation owns an async
session and transaction; PostgreSQL still enforces constraints, row locks and
advisory locks. Psycopg is the SQLAlchemy driver, with no separate driver pool.
PostgreSQL role/database provisioning and Temporal's upstream schemas are managed
by their respective infrastructure packages.

For development, review Alembic's generated Python revision before applying it:

```sh
BOXIE_POSTGRES_DSN_FILE=/path/to/development-dsn uv run --package boxie alembic \
  -c packages/boxie/alembic.ini revision --autogenerate -m "Describe schema change"
BOXIE_POSTGRES_DSN_FILE=/path/to/development-dsn uv run --package boxie alembic \
  -c packages/boxie/alembic.ini check
```

Use Alembic schema operations and SQLAlchemy expressions in revisions; do not
introduce SQL strings. Revisions and the environment are packaged in the Boxie
wheel so the explicit upgrade command also works outside the checkout.

## Retention and ownership

Completed sandbox IDs and idempotency keys remain available for **seven days
after confirmed physical cleanup**. After that window a key may be accepted as a
new request. Delivered outbox records remain for seven days after delivery.
`PostgresLifecycle.purge_retained()` removes these records in dependency order.
It never removes pending cleanup, live slots/references, or undelivered commands.
Artifact retirement tombstones remain; a retired UUID cannot be resurrected.

The supervisor owns artifact bytes. `PostgresArtifactStore` publishes complete fsynced
files under unique storage UUIDs before SQL publication, and never writes local
JSON metadata. Retirement/collection uses the same row lock as admission.
`collect()` retries retired unreferenced file deletion; `collect_orphans()` checks
SQL before removing abandoned uploads/publications after a one-hour grace period
(default), processing at most 100 directory entries per call. Run both on the
sole owning supervisor. An unavailable database authorizes no orphan deletion.
