# Playground persistence

Playground stores preview metadata, revisions, session bindings and unfinished
operations in its own PostgreSQL database. SQLModel and SQLAlchemy handle queries
and transactions; Alembic owns the schema. Boxie still owns artifacts and sandboxes,
accessed only through its HTTP/WebSocket API.

## Domain, application and adapters

The synchronous [domain](../src/cli_playground/domain.py) decides whether a build
can publish and how session attachment claims change. It accepts validated values
and an explicit time, and performs no database, filesystem or network operations.
[Sandbox contracts](../src/cli_playground/sandbox.py) likewise have no transport
implementation.

The async [application service](../src/cli_playground/service.py) coordinates those
decisions with the [persistence ports](../src/cli_playground/ports.py) and Boxie.
The PostgreSQL adapters in [storage.py](../src/cli_playground/storage.py) and
[session_store.py](../src/cli_playground/session_store.py) load values, apply domain
rules and commit them. Domain code does not receive SQL sessions. HTTP and
WebSocket routes call the application service.

Publication locks the PR row while checking build order, closure and immutable
binary identity. It commits the revision, current preview and upload-intent
removal together. PR closure cancels pending uploads in the same transaction;
reopening cannot revive them. Attachment updates lock and reload the binding,
so a late completion cannot overwrite a newer claim. Transactions end before
calling Boxie.

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

Use a private, non-symlink `CLI_PLAYGROUND_POSTGRES_DSN_FILE` with mode `0600`.
The DSN accepts PostgreSQL URI or libpq syntax. For example, a local service on the
same private network can use `host=postgres dbname=cli_playground
user=cli_playground password=YOUR_SECRET`. Never reuse Boxie's database login.

Run upgrades before starting the gateway:

```sh
uv run --locked --package cli-playground python -m cli_playground.migrate \
  --dsn-file "$CLI_PLAYGROUND_POSTGRES_DSN_FILE"
```

The installed wheel includes migration scripts. Startup checks the schema version
and fails if it is missing or incompatible. It does not create or upgrade tables.
To author a later revision from the repository root, use:

```sh
uv run --locked --package cli-playground alembic \
  -c tools/cli-playground/alembic.ini revision --autogenerate -m "Describe the change"
```

Review the generated script before applying it. The initial migration is tested
against the SQLModel metadata to catch schema drift. See the
[infrastructure guide](../infra/README.md#database-and-cutover) for provisioning,
backup and the deliberate fresh-state cutover.
