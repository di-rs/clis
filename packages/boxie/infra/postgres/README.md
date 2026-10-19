# Boxie PostgreSQL deployment

Boxie owns this independently operated PostgreSQL deployment and its persistent
storage. The Compose project is `boxie-postgres`. Boxie,
Temporal persistence, and Temporal visibility each have a distinct database and
non-superuser login. `init-databases.sql` revokes public database access. Only the
PostgreSQL provisioning credential is a superuser; never give it to applications.

Set `BOXIE_CONTROL_NETWORK` and four independently generated secrets:
`POSTGRES_PASSWORD`, `BOXIE_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, and
`VISIBILITY_DB_PASSWORD`. Supply them from operator-owned environment files with
mode 0600. From this directory, run `docker compose up -d --wait`. PostgreSQL
17.11 is pinned to a multi-platform registry manifest digest in `compose.yml`.
There are no published host ports. Supply the operator-owned internal control
network before starting PostgreSQL, using the same network as Boxie and Temporal:

```sh
docker network create --internal "$BOXIE_CONTROL_NETWORK"
```

Create it once per installation, outside every service's Compose project. All
projects consume it as an external network, so stopping PostgreSQL cannot own or
remove their shared network. Attach only trusted service containers. The
PostgreSQL service has no Docker socket mount.

Initialization runs only for an empty volume. Rotate credentials explicitly in
PostgreSQL and the affected service configuration. Preserve `postgres-data` on
releases and ordinary shutdown: **never use `docker compose down --volumes` in
production**. There is no legacy-state migration or backup implementation here.

Apply Boxie's Alembic revisions explicitly with the installed service environment:

```sh
python -m boxie.persistence.migrate --dsn-file /run/secrets/boxie-dsn
```

The DSN uses the `boxie` role/database. Alembic records applied revisions and
upgrades the schema transactionally. Run one migration job at a time before
starting updated service processes; API startup never performs migrations.
The unreleased predecessor's custom migration ledger is not an upgrade source.

Application tables are mapped SQLModel records. Each operation owns an async
session and transaction; PostgreSQL still enforces constraints, row locks and
advisory locks. Psycopg is the SQLAlchemy driver, with no separate driver pool.
PostgreSQL role/database provisioning and Temporal's upstream schemas are managed
by their respective infrastructure packages.

For development, review Alembic's generated Python revision before applying it:

```sh
BOXIE_POSTGRES_DSN_FILE=/path/to/development-dsn uv run --locked --package boxie alembic \
  -c packages/boxie/alembic.ini revision --autogenerate -m "Describe schema change"
BOXIE_POSTGRES_DSN_FILE=/path/to/development-dsn uv run --locked --package boxie alembic \
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

The agent owns artifact bytes. `PostgresArtifactStore` publishes complete fsynced
files under unique storage UUIDs before SQL publication, and never writes local
JSON metadata. Retirement/collection uses the same row lock as admission.
`collect()` retries retired unreferenced file deletion; `collect_orphans()` checks
SQL before removing abandoned uploads/publications after a one-hour grace period
(default), processing at most 100 directory entries per call. Run both on the
sole owning agent. An unavailable database authorizes no orphan deletion.

## Isolated checks

From the repository root:

```sh
UV_CACHE_DIR=/tmp/boxie-uv-cache uv run --locked pytest -m integration packages/boxie/tests/test_postgres.py
```

The fixture requires Docker, creates randomly named disposable containers and
non-superuser test databases, then removes only those test-owned resources.
Default pytest excludes integration and never starts Docker.
