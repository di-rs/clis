# CLI playground

Try a pull request's Rust commands in a browser terminal. Each PR gets a stable
preview URL, and each browser tab gets Bash, sample files, and a disposable
workspace. All included CLIs share `PATH`, so you can try pipelines.

## Use a preview

Open the PR's playground link and choose a command to see a starter example.
Changing the selection keeps your terminal and files. A new build offers
**Switch to latest build** without replacing a running session automatically.
**Reset** ends the session.

A connection drop can resume the same terminal on the current page. Reloading the
page starts fresh. Sessions expire after 60 minutes, ten idle minutes, or two
minutes disconnected, whichever limit applies first. Sandboxes have no network.

## Run or develop it

The UI uses Svelte and TypeScript; the server uses Python/FastAPI. Boxie runs the
sandboxes as a separate service. You need a private Boxie endpoint, a consumer
credential, and PostgreSQL before starting the backend.

Follow [local development](docs/local-development.md) for frontend build commands
and service configuration, or [infrastructure](infra/README.md) for Compose and
production setup. Native sandbox execution requires Linux and gVisor.

## How a preview reaches the browser

The [PR build workflow](../../.github/workflows/cli-playground.yml) compiles the
affected CLIs. A separate [trusted publisher](../../.github/workflows/cli-playground-preview.yml)
uploads the binaries and updates the PR link. Neither step creates a browser
sandbox; Boxie starts one when a visitor opens a terminal.

Build selection includes changes to a CLI's source or resolved dependencies.
Global build settings can select all CLIs. This differs from benchmark selection,
which requires changes to the CLI's own implementation.

## Work on the service

Read [persistence](docs/persistence.md) before changing session recovery or
metadata storage, and [request tracing](docs/requests.md) to diagnose an action
across services. The [PR workflow](../../.github/workflows/pull-request-check.yml)
checks Python, frontend, and browser behavior; the
[native lifecycle workflow](../../.github/workflows/sanctuary-lifecycle.yml)
checks the Linux/gVisor deployment.

[Repository architecture](../../docs/architecture.md)
