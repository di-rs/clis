# Private PostgreSQL platform

This independently operated package owns persistent PostgreSQL storage. Boxie,
Temporal persistence, and Temporal visibility each have a distinct database and
non-superuser login. `init-databases.sql` revokes public database access. Only the
platform provisioning credential is a superuser; never give it to applications.

First provision the shared host and storage with the
[single-host environment](../environments/single-host/README.md). Run the explicit
platform resource preparation with its exported plan before starting Compose.
Set `BOXIE_CONTROL_NETWORK`, `POSTGRES_VOLUME`, and four independently generated secrets:
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

The production API, worker and agent use the durable PostgreSQL/Temporal
configuration. Run schema management before their release; ordinary process
startup and application rollback never reverse schema changes.

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

Platform owns the control, consumer, metrics, Grafana-origin and Temporal-origin
bridges. `python3 resources.py --plan resources.json` runs the explicit preparation
step on the target host. The plan comes from the environment’s `sharedResources`
output; `resources.py` is `platform_infra/resources.py` copied from the same source
revision. It requires the bootstrap-installed `boxie-storage-guard` and an attached,
mounted persistent disk. It validates existing network ownership, bridge driver
and isolation, volume ownership and backing directory, and numeric directory
owners before creating absent resources. Conflicting resources fail closed; the
command never adopts, repairs or deletes them and never starts a container.

`POSTGRES_VOLUME` is external to Compose and backed by a directory on the
persistent disk. All shared data volumes have the same independent platform
ownership, and application releases preserve them. The environment plan supplies
the required directory owners (PostgreSQL 999, Boxie/Playground
10001, Vector 65532, VictoriaLogs/Prometheus 65534, Grafana 472). The installation
plan creates only the selected product volumes.

The host package provisions Hetzner resources, pinned SSH identity and first-boot
Docker/gVisor setup. Docker’s data root is on `/srv/boxie`; its systemd prestart
guard verifies the exact persistent volume device. Missing storage prevents Docker
startup, including after reboot, so a missing disk cannot silently redirect state
to the host root filesystem. The host deployment key remains administrative
access. Keep it in the deployment environment’s secret store, separate from the
public `{host, host_public_key, identity_ref}` connection profile.

The isolated service fixture creates only its random named bridges, checks
ownership/internal state and removes only its recorded owned network IDs.

Host bootstrap also installs the environment-supplied observability journal policy
in `/etc/systemd/journald.conf.d/60-clis.conf`. The canonical policy uses persistent
storage, a 1 GiB persistent target, a 128 MiB runtime target, 4 GiB free-space
headroom, two-day retention and hourly rotation. Platform validates that the
supplied policy has finite explicit limits; it does not import observability.
Bootstrap installs journal ACL tools, prepares persistent journal directories,
grants the normal `systemd-journal` group read/traversal access and inherited
ACLs, makes machine identity readable, restarts journald and flushes it. Each
systemd/ACL activation command has a 60-second timeout. The policy file and ACLs
survive reboot. `/etc/boxie-platform/journal-group-gid` contains the host-discovered
public numeric GID for `OBS_JOURNAL_GROUP_GID`; never guess it from a local host.
