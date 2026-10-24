# Playground database and recovery

Playground stores preview metadata, revisions, session bindings and unfinished
operations in its own PostgreSQL database. Boxie still owns artifacts and sandboxes,
accessed only through its HTTP/WebSocket API.

## Transaction and recovery guarantees

Publication locks the PR row while checking build order, closure and immutable
binary and catalog identity. It commits the revision, current preview and upload-intent
removal together. PR closure cancels pending uploads in the same transaction;
reopening cannot revive them. Attachment updates lock and reload the binding,
so a late completion cannot overwrite a newer claim. Transactions end before
calling Boxie.

The nullable `catalog` JSONB and `catalog_sha256` fields on `artifact_revision`
and `upload_intent` were added by `0003_cli_catalog`. A catalog's canonical digest
participates in conflict checks before remote upload, during recovery, and after
publication. A changed catalog cannot reuse a revision whose executable bytes
are unchanged. Existing rows need no backfill and remain usable without a catalog.
The staged ZIP contains executable bytes only; recovery reads the catalog from
the persisted upload intent.

### Recover unfinished remote operations

Remote operations still need recovery. Playground persists an intent before
uploading an artifact or requesting a sandbox, and retries with the same UUID.
Unclaimed admissions are compensated by stopping their sandboxes. Stops and
artifact retirements remain pending until Boxie confirms them. A database error
is an unavailable response, never an empty state or permission to discard work.

## What stays on disk

`CLI_PLAYGROUND_DATA_DIR/uploads/` contains ZIPs needed to retry unfinished
uploads. A ZIP is synced and renamed before its database intent is committed;
it is removed after publication or compensation commits. Startup removes ZIPs
without an intent only after successfully reading the database. Temporary
validation directories also live beneath the data directory.

Structured state has no JSON-file fallback. Keep one Uvicorn worker: admission
tracking, upload serialization and startup recovery still assume a single gateway.
PostgreSQL alone does not make replicas safe.

## Schema changes

Use the database connection configured during [local setup](../README.md#configure-the-backend).
SQLModel and SQLAlchemy handle queries and transactions; Alembic owns the schema.

Run upgrades before starting the gateway:

```sh
uv run --package cli-playground alembic \
  -c tools/cli-playground/alembic.ini upgrade head
```

The installed wheel includes Alembic scripts; the container includes `alembic.ini`
and runs the standard `alembic upgrade head` command. Startup checks the schema version
and fails if it is missing or incompatible. It does not create or upgrade tables.

Catalog rollout requires the migration and compatible gateway before enabling
the PR build variable; follow the [publication sequence](previews.md#enable-generated-command-catalogs).
The startup schema check expects `0003_cli_catalog`. An older application may
reject the newer schema on restart, so retain the compatible application when
disabling catalog production.

### Author a migration

To author a later revision from the repository root, use:

```sh
uv run --package cli-playground alembic \
  -c tools/cli-playground/alembic.ini revision --autogenerate -m "Describe the change"
```

Review the generated script before applying it. The migrated schema is tested
against the SQLModel metadata to catch schema drift. See the
[database provisioning](#database-and-cutover) for provisioning,
backup and the deliberate fresh-state cutover.

## Database and cutover

Provision a dedicated `cli_playground` database and login on the existing
PostgreSQL service. For the independent local unit, have its operator create the
database, grant ownership to that login, revoke public database access, and provide
a mode-`0600` file containing `postgresql+psycopg://USER:PASSWORD@postgres/cli_playground`.
`CLI_PLAYGROUND_DATABASE_URL_FILE` names that host file; Compose mounts it at
`/run/secrets/CLI_PLAYGROUND_DATABASE_URL` for Pydantic Settings. PostgreSQL must
be reachable through `CLI_PLAYGROUND_DATABASE_NETWORK`.
The database network contains only PostgreSQL and the gateway. Keep it separate
from Boxie's control network; the proxy does not join it.

### Shared-host provisioning

Shared-host setup generates a separate password and database URL under
`/etc/sanctuary/cli-playground/`. Its `provision.sh` adds the database using the
existing PostgreSQL administrator inside the database container. This works with
already-initialized PostgreSQL volumes; it does not depend on first-boot scripts.
The application receives only its own database URL, never the administrator password.

`install.sh` reconciles the PostgreSQL Compose service to attach its internal
`playground` network, which may restart PostgreSQL on an existing host. Schedule
this first installation during the cutover window. It then provisions the database
and runs Alembic before startup. `deploy.sh`
runs the migration command from the new image before replacing the backend. A
failed upgrade stops the deployment. Neither path enables multiple workers.
For an existing host receiving this change, install the updated owner scripts and
rendered settings before its first database-backed release.

### Replace file-based metadata

There is no importer for old metadata files. For the initial cutover:

1. Pause preview publication and new browser sessions at the ingress.
2. Let the old backend finish pending uploads and stops. Drain its sessions or
   wait for their bounded sandbox lifetimes before stopping it.
3. Keep the old data volume for rollback. Provision the new database, apply its
   schema and select a fresh data volume for ZIP staging.
4. Start the new backend, verify readiness, then re-enable traffic and rerun the
   PR builds to republish previews. Old session bindings are not carried over.

## Back up and restore

Back up the database and staging volume together while the gateway is stopped.
Restore both from the same backup before starting recovery. Boxie's artifact
storage and database retain their separate backup requirements. Database backups
alone cannot recover bytes for an upload Boxie has not acknowledged.

## Roll back the initial cutover

Rollback is a coordinated state restore, not an Alembic downgrade: drain any new
sessions and pending operations, stop the new backend, then restore the prior
application/configuration and its matching old data volume. Old metadata files
cannot describe sessions or previews created after cutover. A schema downgrade
on a disposable test database is only a migration check.
