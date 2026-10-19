# CLI playground

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

Architecture: Svelte + strict TypeScript + Vite+ static SPA, plain CSS, Python/FastAPI;
no SSR, production Node server or Tailwind.

Defaults: seven-day previews, 60-minute absolute sessions, 10-minute idle timeout,
two-minute disconnected timeout, eight concurrent sandboxes;
per session: one CPU, 512 MiB RAM, 64 MiB workspace, 16 MiB terminal output, no network.

## Run locally

Playground connects to an independently operated private Boxie endpoint. Follow
[local infrastructure](infra/README.md) and the [Boxie setup](../../packages/boxie/infra/README.md).
Use native Linux with gVisor for interactive sandboxes. Docker Desktop can build
images and run the product UI against an independently supplied Boxie service,
but does not provide the required sandbox runtime.

```sh
uv sync --locked --all-packages
pnpm --dir tools/cli-playground/frontend install --frozen-lockfile
pnpm --dir tools/cli-playground/frontend build
```

The Python package lives in `src/playground/`. The frontend build places real assets
in `src/playground/static/`, included in the installed
wheel and sdist. To run the installed service, set `PLAYGROUND_DATA_DIR`,
`PLAYGROUND_PUBLIC_URL`, `PLAYGROUND_DEPLOY_TOKEN`, `PLAYGROUND_BOXIE_URL`, and
`PLAYGROUND_BOXIE_CREDENTIAL_FILE` (a private regular file):

```sh
uv run --locked --package cli-playground uvicorn playground.app:create_app \
  --factory --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

No Docker client, Docker socket or Boxie implementation is installed in the
Playground image. Its server-side consumer credential is not sent to the browser.
Production activation is unavailable until PR4; setup currently provisions only.
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

## Production releases

Use the [shared-host guide](../../infra/environments/single-host/README.md) for
setup, deployment gates and failure inspection. Releases are manual: one workflow
builds and deploys Boxie, the Playground sandbox and the gateway from the same
commit. Preview publication retains its separate trust boundary.

## Workflow responsibilities

| Workflow | Responsibility |
| --- | --- |
| [🏗️ Playground PR binary builds](../../.github/workflows/cli-playground.yml) | Compile affected PR binaries without deployment credentials |
| [🌐 Playground PR previews](../../.github/workflows/cli-playground-preview.yml) | Trusted build-state updates, artifact publication and PR close/reopen reconciliation |
| [🚀 Boxie Runtime and Sandbox Release](../../.github/workflows/cli-playground-release.yml) | Manually build and deploy Boxie, sandbox and gateway together |
| [☁️ Playground infrastructure setup](../../.github/workflows/cli-playground-setup.yml) | Manually provision infrastructure and install services |
| [🧪 Boxie lifecycle integration](../../.github/workflows/boxie-lifecycle.yml) | Verify PostgreSQL/Temporal recovery and native Linux/gVisor lifecycle and browser faults |

PR publication stores a binary batch and preview metadata. It does not deploy
the runtime or start a sandbox; each browser terminal starts its own sandbox.
The build and preview workflows remain separate because they execute with
different trust and credentials.

See [Testing](../../docs/testing.md) for Python, frontend and browser checks,
and the [🧪 Boxie lifecycle integration workflow](../../.github/workflows/boxie-lifecycle.yml)
for real PostgreSQL/Temporal recovery and native Linux/gVisor browser checks.
