# Playground database operations

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

There is no importer for old metadata files. For the initial cutover:

1. Pause preview publication and new browser sessions at the ingress.
2. Let the old backend finish pending uploads and stops. Drain its sessions or
   wait for their bounded sandbox lifetimes before stopping it.
3. Keep the old data volume for rollback. Provision the new database, apply its
   schema and select a fresh data volume for ZIP staging.
4. Start the new backend, verify readiness, then re-enable traffic and rerun the
   PR builds to republish previews. Old session bindings are not carried over.

Back up the database and staging volume together while the gateway is stopped.
Restore both from the same backup before starting recovery. Boxie's artifact
storage and database retain their separate backup requirements. Database backups
alone cannot recover bytes for an upload Boxie has not acknowledged.

Rollback is a coordinated state restore, not an Alembic downgrade: drain any new
sessions and pending operations, stop the new backend, then restore the prior
application/configuration and its matching old data volume. Old metadata files
cannot describe sessions or previews created after cutover. A schema downgrade
on a disposable test database is only a migration check.
