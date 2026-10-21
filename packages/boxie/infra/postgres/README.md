# PostgreSQL for Boxie

This Compose project stores Boxie's sandbox state and Temporal's persistence and
visibility databases. Each service has its own database and non-superuser login.
Only the provisioning credential is a PostgreSQL administrator.

## Start a fresh database

Prepare an operator-owned internal Docker network and a private environment file
(mode `0600`) containing `BOXIE_CONTROL_NETWORK`, `POSTGRES_PASSWORD`,
`BOXIE_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, and `VISIBILITY_DB_PASSWORD`.
Generate a different password for each role.

Create the network once, then start Compose from this directory with those
variables loaded:

```sh
docker network create --internal "$BOXIE_CONTROL_NETWORK"
docker compose up -d --wait
```

The [manifest](compose.yml) pins the PostgreSQL image and publishes no host port.
Initialization runs only on an empty volume. Existing installations need explicit
credential rotation in PostgreSQL and the affected service configuration.

## Apply Boxie migrations

Using the installed Boxie environment and its private DSN file:

```sh
python -m boxie.persistence.migrate --dsn-file /run/secrets/boxie-dsn
```

Run one migration job before starting updated Boxie processes. API startup does
not migrate. Temporal owns its separate upstream schemas. See
[schema and retention](storage.md) for development migrations, cleanup rules,
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
