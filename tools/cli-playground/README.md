# cli-playground

Run a PR's native Rust CLIs in a browser terminal. Each PR has one stable URL
containing all affected CLIs; each tab gets Bash, sample files and a disposable
workspace. The selector changes starter commands without reconnecting or losing
files. All CLIs share `PATH` for pipelines. A new PR build leaves existing
terminals and files intact; the page offers **Switch to latest build**. Reset ends the session. Reconnect resumes the same terminal on the current page; a detected disconnection starts a two-minute cleanup deadline.

The page shows the latest build state independently of the running revision.
Building, failed and cancelled builds can retain an explicitly labeled last
successful build. A revision with no affected CLIs opens a status page instead
of a new terminal. The page checks for updates every ten seconds.

PR builds select CLIs with source changes, changed resolved dependencies, or a
changed shared library in their dependency graph. Lockfile edits for another CLI
do not select unrelated binaries. Global build settings, including the root
Cargo manifest and Rust toolchain, still select all CLIs. Dependency analysis uses
the same [lockfile rules as benchmarks](../../docs/benchmarking.md#verification-and-scope).
Documentation, tests, workflows, and Python tooling edits do not add CLIs to the
build selection.

Architecture: Svelte + strict TypeScript + Vite+ static SPA, plain CSS, Python/FastAPI;
no SSR, production Node server or Tailwind.

Defaults: seven-day previews, 60-minute absolute sessions, 10-minute idle timeout,
two-minute disconnected timeout, eight concurrent sandboxes;
per session: one CPU, 512 MiB RAM, 64 MiB workspace, 16 MiB terminal output, no network.

## Run locally

cli-playground connects to an independently operated private Boxie endpoint. Follow
[local infrastructure](infra/README.md) and the [Boxie setup](../../packages/boxie/infra/README.md).
Use native Linux with gVisor for interactive sandboxes. Docker Desktop can build
images and run the product UI against an independently supplied Boxie service,
but does not provide the required sandbox runtime.

```sh
uv sync --locked --all-packages
pnpm --dir tools/cli-playground/frontend install --frozen-lockfile
pnpm --dir tools/cli-playground/frontend build
```

The Python package lives in `src/cli_playground/`. The frontend build places real assets
in `src/cli_playground/static/`, included in the installed
wheel and sdist. To run the installed service, set `CLI_PLAYGROUND_DATA_DIR`,
`CLI_PLAYGROUND_PUBLIC_URL`, `CLI_PLAYGROUND_DEPLOY_TOKEN`, `CLI_PLAYGROUND_BOXIE_URL`,
`CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE` and `CLI_PLAYGROUND_DATABASE_URL`. Keep the
Boxie credential in a private regular file with mode `0600`. The database URL uses
`postgresql+psycopg://USER:PASSWORD@HOST/DATABASE`; Pydantic Settings also reads it
from `/run/secrets/CLI_PLAYGROUND_DATABASE_URL`. Provision a separate PostgreSQL
database and apply its schema before startup:

```sh
uv run --locked --package cli-playground alembic \
  -c tools/cli-playground/alembic.ini upgrade head
uv run --locked --package cli-playground uvicorn cli_playground.app:create_app \
  --factory --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

No Docker client, Docker socket or Boxie implementation is installed in the
cli-playground image. Its server-side consumer credential is not sent to the browser.
The terminal keeps credentials, applied output sequence and screen in page memory.
Reconnect drains pending xterm writes, resumes retained output exactly once and
synchronizes geometry before enabling user input. Input typed while disconnected
is discarded. An expired session or output history gap offers a fresh sandbox.
A page reload always starts fresh; there is no persisted cursor or screen snapshot.

Input provenance uses the isolated `terminal/input-intent.ts` adapter against the
pinned xterm 6.0.0 internal `triggerDataEvent` source flag. It checks version and
runtime shape and fails clearly on unsupported bundles. Revalidate keyboard,
paste, asynchronous composition and parser replies with the actual browser suite
when upgrading xterm. This private dependency keeps terminal-generated replies
working during replay without counting them as user activity. Browser tests drive
DOM composition events; they do not claim OS-native IME coverage.

See [persistence](docs/persistence.md) for the synchronous domain, async ports and
PostgreSQL adapters, recovery rules and schema workflow. Metadata belongs to
PostgreSQL; the data directory retains pending upload ZIPs.

## Production releases

Use the [shared-host guide](../../infra/environments/single-host/README.md) for
setup, deployment gates and failure inspection. Releases are manual. The cli-playground release builds and deploys its backend and
frontend together. A separate Boxie release delivers the Boxie and sandbox images.
Preview publication retains its separate trust boundary.

## Diagnose a failed request

Copy the `X-Request-ID` response header from the failed HTTP request and search
`request_id:="<UUID>"` in Grafana Explore's VictoriaLogs datasource. The public
boundary always generates this ID, forwards it to Boxie and the supervisor, and
includes it in bounded JSON service logs. Internal URLs, credentials, terminal
content and exception text are omitted. Logging drops diagnostics under pressure
without blocking requests.

WebSocket connections also have one server-generated ID, forwarded on the
internal handshake. Browser JavaScript cannot read handshake headers. A reconnect
starts a new action; `sandbox_id` still identifies the same terminal resource.

Saved admission, upload, stop and retirement operations retain their first
request IDs in PostgreSQL so later retries can be traced to the action that
started them. These optional diagnostic fields leave existing rows with an
unknown origin. Apply the [schema upgrade](docs/persistence.md#schema-changes)
before starting the gateway. The
[host guide](../../infra/environments/single-host/README.md#search-across-services)
covers the optional collector extension and rollout order.

## Workflow responsibilities

| Workflow | Responsibility |
| --- | --- |
| [🏗️ cli-playground PR binary builds](../../.github/workflows/cli-playground.yml) | Compile affected PR binaries without deployment credentials |
| [🌐 cli-playground PR previews](../../.github/workflows/cli-playground-preview.yml) | Trusted build-state updates, artifact publication and PR close/reopen reconciliation |
| [🚀 cli-playground Release](../../.github/workflows/cli-playground-release.yml) | Manually build and deploy cli-playground Backend and Frontend |
| [🚀 Boxie and Sandbox Release](../../.github/workflows/boxie-release.yml) | Manually build and deploy the Boxie and sandbox images together |
| [☁️ Single-host Sanctuary setup](../../.github/workflows/sanctuary-setup.yml) | Manually provision infrastructure and install services |
| [🧪 Boxie lifecycle integration](../../.github/workflows/boxie-lifecycle.yml) | Verify PostgreSQL/Temporal recovery and native Linux/gVisor lifecycle faults |

PR publication stores a binary batch and preview metadata. It does not deploy
Boxie or start a sandbox; each browser terminal starts its own sandbox.
The build and preview workflows remain separate because they execute with
different trust and credentials.

See [Testing](../../docs/testing.md) for Python, frontend and browser checks,
and the [🧪 Boxie lifecycle integration workflow](../../.github/workflows/boxie-lifecycle.yml)
for real PostgreSQL/Temporal recovery. The separate
[shared-system lifecycle workflow](../../.github/workflows/sanctuary-lifecycle.yml)
adds native Linux/gVisor browser checks.
