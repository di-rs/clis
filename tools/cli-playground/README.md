# CLI playground

Run a PR's native Rust CLIs in a browser terminal. Each PR has one stable URL
containing all affected CLIs; each tab gets Bash, sample files and a disposable
workspace. The selector changes starter commands without reconnecting or losing
files. All CLIs share `PATH` for pipelines. A new PR build leaves existing
terminals and files intact; the page offers **Switch to latest build**. Reset ends the session; a detected disconnection starts a two-minute cleanup deadline.

The page shows the latest build state independently of the running revision.
Building, failed and cancelled builds can retain an explicitly labeled last
successful build. A revision with no affected CLIs opens a status page instead
of a new terminal. The page checks for updates every ten seconds.

Architecture: Svelte + strict TypeScript + Vite+ static SPA, plain CSS, Python/FastAPI;
no SSR, production Node server or Tailwind.

Defaults: seven-day previews, 60-minute absolute sessions, 10-minute idle timeout,
two-minute disconnected timeout, eight concurrent sandboxes;
per session: one CPU, 512 MiB RAM, 64 MiB workspace, 16 MiB terminal output, no network.

## Run locally

Playground connects to an independently operated private Boxie endpoint. Follow
[local infrastructure](infra/README.md) and the [Boxie setup](../boxie/infra/README.md).
Use native Linux with gVisor for interactive sandboxes. Docker Desktop can build
images and run the product UI against an independently supplied Boxie service,
but does not provide the required sandbox runtime.

```sh
uv sync --locked --all-packages
pnpm --dir tools/cli-playground/frontend install --frozen-lockfile
pnpm --dir tools/cli-playground/frontend build
```

The build places real assets in `playground/static/`, included in the installed
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
PR1's Boxie direct lifecycle and agent-local recovery are transitional until PR2
adds PostgreSQL/Temporal authority. A page reload offers a fresh sandbox; terminal
reattachment and output replay are outside PR1.

## Production transition

Production activation is unavailable until PR4. The [hosting reference](infra/README.md)
retains protected host/DNS provisioning; setup now provisions only. Release and
all deploy entrypoints fail before state reads, publication or activation. Keep
automatic release disabled through this transition. Existing preview-controller
trust boundaries remain unchanged; local Compose and native runsc CI validate the
split runtime independently.

## Workflow responsibilities

| Workflow | Responsibility |
| --- | --- |
| [CLI playground build](../../.github/workflows/cli-playground.yml) | Compile affected PR binaries without deployment credentials |
| [CLI playground previews](../../.github/workflows/cli-playground-preview.yml) | Trusted build-state updates, artifact publication and PR close/reopen reconciliation |
| [CLI playground release](../../.github/workflows/cli-playground-release.yml) | Fails explicitly until split-service release support in PR4 |
| [CLI playground setup](../../.github/workflows/cli-playground-setup.yml) | Manually provision infrastructure only |
| [CLI playground sandbox](../../.github/workflows/cli-playground-sandbox.yml) | Verify actual PTY execution with Docker/gVisor on Linux |

PR publication stores a binary batch and preview metadata. It does not deploy
the runtime or start a sandbox; each browser terminal starts its own sandbox.
The build and preview workflows remain separate because they execute with
different trust and credentials.

See [Testing](../../docs/testing.md) for Python, frontend and browser checks,
and the [Linux smoke workflow](../../.github/workflows/cli-playground-sandbox.yml)
for the real Docker/gVisor check.
