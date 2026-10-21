# PostgreSQL for Boxie

This Compose project stores Boxie's sandbox state and Temporal's persistence and
visibility databases. Each service has its own database and non-superuser login.
Only the provisioning credential is a PostgreSQL administrator.

## Start a fresh database

Prepare an operator-owned internal Docker network and a private environment file
(mode `0600`) containing `BOXIE_CONTROL_NETWORK`, `POSTGRES_PASSWORD`,
`BOXIE_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, and `VISIBILITY_DB_PASSWORD`.
Generate a different password for each role.

Create the network once, then start Compose from `packages/boxie/infra/postgres/` with those
variables loaded:

```sh
docker network create --internal "$BOXIE_CONTROL_NETWORK"
docker compose up -d --wait
```

The [manifest](../infra/postgres/compose.yml) pins the PostgreSQL image and publishes no host port.
Initialization runs only on an empty volume. Existing installations need explicit
credential rotation in PostgreSQL and the affected service configuration.

## Apply Boxie migrations

Using the installed Boxie environment and its private DSN file:

```sh
python -m boxie.persistence.migrate --dsn-file /run/secrets/boxie-dsn
```

Migration sources live in `packages/boxie/alembic/`, alongside `alembic.ini`, and
are included in the installed package. From the repository root, use
`uv run --package boxie python -m boxie.persistence.migrate --dsn-file /etc/boxie/postgres-dsn.secret`.

Migration `0002_command_outbox` preserves pending start/stop commands and removes
obsolete observation commands and dispatcher leases. Its predecessor is undeployed;
incompatible live workflows need a separately reviewed drain or versioned cutover.

Run one migration job before starting updated Boxie processes. API startup does
not migrate. Temporal owns its separate upstream schemas. See
[schema and retention](#boxie-schema-and-retention) for development migrations, cleanup rules,
and artifact reconciliation.

## Preserve data

Keep `postgres-data` during releases and shutdown. Never use
`docker compose down --volumes` in production. This package does not implement
backups; arrange and test them before relying on the deployment.

For isolated checks, run from the repository root:

```sh
uv run pytest -m integration packages/boxie/tests/test_postgres.py
```

These tests require Docker and create disposable databases. Default pytest
excludes integration tests.

## Boxie schema and retention

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

## Recovery prerequisite for production activation

Backup and restore implementation belongs to the production activation increment.
Before enabling deployment, demonstrate restoration of all three PostgreSQL
databases (Boxie, Temporal persistence and Temporal visibility) and Boxie's artifact
data into a fresh installation, using off-host backups and a documented recovery
point. Verify application accounting, artifact references and Temporal workflow
execution after restoration. Keep the state-backend passphrase and required
certificate material recoverable separately from the running host. Pulumi resource
protection and same-host Docker volumes do not provide this recovery evidence.
