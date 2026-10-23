# CLI playground

Try a pull request's Rust commands in a browser terminal. Each PR gets a stable
preview URL, and each browser tab gets Bash, sample files, and a disposable
workspace. All included CLIs share `PATH`, so you can try pipelines.

## Use a preview

Open the PR's playground link and choose a CLI in the left command explorer.
Search commands and flags, or choose **Changes** to inspect additions, edits, and
removals against the PR's recorded merge base. Expand **Details** for defaults,
choices, full help, and before/after values. Removed entries cannot be inserted.

Click a command to insert its executable and subcommand path at the terminal
cursor. Click a flag to insert its spelling. Neither action adds values or presses
Enter; finish the command yourself. Sample files and shell essentials are in
collapsed sections below the explorer. On narrow screens, **Explore** opens a drawer.

Selection, search, and drawer changes preserve your terminal, partially typed
input, and files. The reference stays pinned to the terminal's build. A new build
offers **Switch to latest build**; switching builds or choosing **Reset session**
ends the old session and starts a fresh workspace.

Older previews show **Command reference unavailable**. An existing CLI whose
merge base cannot export metadata shows **Comparison unavailable**, with no change
badges. Newly introduced CLIs are marked added. The explorer describes the
published CLIs' declared interfaces; it cannot detect runtime behavior or custom
validation changes that the parser metadata does not express.

A connection drop can resume the same terminal on the current page. Reloading the
page starts fresh. Sessions expire after 60 minutes, ten idle minutes, or two
minutes disconnected, whichever limit applies first. Sandboxes have no network.

## How a preview reaches the browser

The [PR build workflow](../../.github/workflows/cli-playground.yml) compiles the
affected CLIs. A separate [trusted publisher](../../.github/workflows/cli-playground-preview.yml)
uploads the binaries and updates the PR link. Neither step creates a browser
sandbox; Boxie starts one when a visitor opens a terminal.

Build selection includes changes to a CLI's source or resolved dependencies.
Global build settings can select all CLIs. This differs from benchmark selection,
which requires changes to the CLI's own implementation.

## Run locally

The UI uses Svelte and TypeScript; the server uses Python/FastAPI. Before starting
the backend, provision a private [Boxie service](../../packages/boxie/docs/deployment.md)
and a separate PostgreSQL database. [Playground infrastructure](docs/deployment.md)
covers Compose and production deployment.

Interactive sandboxes require native Linux with gVisor. Docker Desktop can build
images and run the UI against a separately operated Boxie service, but cannot
provide the required sandbox runtime.

### Build the frontend

Run from the repository root:

```sh
uv sync --all-packages
pnpm --dir tools/cli-playground/frontend install
pnpm --dir tools/cli-playground/frontend build
```

The build writes assets to `src/cli_playground/static/` within this package.
Those assets are included in the Python wheel and sdist.

### Configure the backend

Set these values before starting the service:

| Variable | Purpose |
| --- | --- |
| `CLI_PLAYGROUND_DATA_DIR` | Storage for pending upload ZIPs |
| `CLI_PLAYGROUND_PUBLIC_URL` | Public playground URL |
| `CLI_PLAYGROUND_DEPLOY_TOKEN` | Credential for publishing previews |
| `CLI_PLAYGROUND_BOXIE_URL` | Private Boxie API URL |
| `CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE` | Consumer secret in a private regular file, mode `0600` |
| `CLI_PLAYGROUND_DATABASE_URL` | PostgreSQL connection URL, using `postgresql+psycopg://USER:PASSWORD@HOST/DATABASE` |

The database URL can instead be stored in
`/run/secrets/CLI_PLAYGROUND_DATABASE_URL`. Environment values take precedence.
Use a database login separate from Boxie's.

### Migrate and start

Apply the schema before starting the gateway:

```sh
uv run --package cli-playground alembic \
  -c tools/cli-playground/alembic.ini upgrade head
uv run --package cli-playground uvicorn cli_playground.app:create_app \
  --factory --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

Keep one worker. Upload serialization and startup recovery assume a single
gateway; PostgreSQL alone does not make multiple replicas safe.

The playground talks to Boxie through its API. Its image contains no Docker
client, Docker socket, or Boxie implementation, and the consumer secret stays
on the server.

## Terminal recovery

The browser keeps terminal credentials, the last applied output sequence, and
screen contents in page memory. On reconnect, it drains pending xterm writes,
replays retained output exactly once, and synchronizes terminal dimensions before
enabling input. Input typed while disconnected is discarded.

The server allocates Boxie's increasing attachment order from its persisted page
claim. Browser frames cannot select or override that order.

An expired session or a gap in retained output offers a fresh sandbox. There is
no persisted cursor or screen snapshot to restore after a page reload.

## Develop the service

### Change terminal input handling

The `terminal/input-intent.ts` adapter uses the pinned xterm 6.0.0 internal
`triggerDataEvent` source flag to distinguish user input from terminal-generated
replies. Those replies must keep working during replay without renewing idle time.
The adapter checks the version and runtime shape and fails on unsupported bundles.

When upgrading xterm, run the browser suite for keyboard input, paste, asynchronous
composition, and parser replies. Its DOM composition tests do not cover OS-native
input method editors (IMEs).

### Change storage or diagnose failures

Read [persistence](docs/database.md) before changing session recovery or
metadata storage, and [request tracing](docs/observability.md) to diagnose an action
across services. The [PR workflow](../../.github/workflows/pull-request-check.yml)
checks Python, frontend, and browser behavior; the
[native lifecycle workflow](../../.github/workflows/sanctuary-lifecycle.yml)
checks the Linux/gVisor deployment.

## Documentation

- [Architecture](docs/architecture.md)
- [Database](docs/database.md)
- [Observability](docs/observability.md)
- [Deployment](docs/deployment.md)
- [Previews](docs/previews.md)

[Repository architecture](../../docs/architecture.md)
