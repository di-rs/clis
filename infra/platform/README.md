# Private PostgreSQL platform

This independently operated package owns persistent PostgreSQL storage. Boxie,
Temporal persistence, and Temporal visibility each have a distinct database and
non-superuser login. `init-databases.sql` revokes public database access. Only the
platform provisioning credential is a superuser; never give it to applications.

Set `BOXIE_CONTROL_NETWORK` and four independently generated secrets:
`POSTGRES_PASSWORD`, `BOXIE_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, and
`VISIBILITY_DB_PASSWORD`. Supply them from operator-owned environment files with
mode 0600. From this directory, run `docker compose up -d --wait`. PostgreSQL
17.11 is pinned to a multi-platform registry manifest digest in `compose.yml`.
There are no published host ports. The named control network is internal; attach
only trusted service containers. The platform has no Docker socket mount.

Initialization runs only for an empty volume. Rotate credentials explicitly in
PostgreSQL and the affected service configuration. Preserve `postgres-data` on
releases and ordinary shutdown: **never use `docker compose down --volumes` in
production**. There is no legacy-state migration or backup implementation here.

Apply Boxie's SQL release explicitly with the installed service environment:

```sh
python -m boxie.persistence.migrate --dsn-file /run/secrets/boxie-dsn
```

The DSN uses the `boxie` role/database, with a connection timeout appropriate to
the private network. Migrations serialize under a PostgreSQL advisory lock,
verify the SHA-256 of every previously applied migration, and commit each new
numbered migration atomically. API startup must never perform migrations.

The current service bootstraps remain direct until the coordinated PR2 cutover;
SQL adapters are explicitly injected by tests. A later cutover must switch
admission, agent artifacts, fenced operations, and worker dispatch together.

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
UV_CACHE_DIR=/tmp/boxie-uv-cache uv run --locked pytest -m integration tools/boxie/tests/test_postgres.py
```

The fixture requires Docker, creates randomly named disposable containers and
non-superuser test databases, then removes only those test-owned resources.
Default pytest excludes integration and never starts Docker.
