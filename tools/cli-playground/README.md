# CLI playground

Run a PR's native Rust CLIs in a browser terminal. Each PR has one stable URL
containing all affected CLIs; each tab gets Bash, sample files and a disposable
workspace. The selector changes starter commands without reconnecting or losing
files. All CLIs share `PATH` for pipelines. Reset, disconnect or a new PR build
ends the session and discards its files.

Architecture: Svelte + strict TypeScript + Vite+ static SPA, plain CSS, Python/FastAPI;
no SSR, production Node server or Tailwind.

Defaults: seven-day previews, 15-minute sessions, eight concurrent sessions;
per session: one CPU, 512 MiB RAM, 64 MiB workspace, 16 MiB terminal output, no network.

## Run locally

Use Docker with Linux containers, uv 0.13+, Node 26.11.1, pnpm 12.9.1, `zip` and `curl`.
Run from the repository root. The sandbox needs x86_64 Linux binaries;
Docker can emulate amd64 on Apple Silicon.

```sh
uv sync --locked --all-packages
pnpm --dir tools/cli-playground/frontend install --frozen-lockfile
pnpm --dir tools/cli-playground/frontend build
docker build --platform linux/amd64 -t clis-playground-sandbox:local \
  -f tools/cli-playground/Sandbox.Dockerfile .
docker build --platform linux/amd64 --build-arg CLI=catr \
  --output type=local,dest=target/playground-linux \
  -f tools/cli-playground/Build.Dockerfile .
```

Export a nonempty `PLAYGROUND_DEPLOY_TOKEN` with the same value in both terminals.
Start the gateway. Docker must see the data directory at the same absolute path;
Docker Desktop shares the checkout by default. This localhost example uses
`runc` because Docker Desktop has no gVisor runtime.

```sh
export PLAYGROUND_DATA_DIR="$PWD/target/playground-data"
export PLAYGROUND_PUBLIC_URL=http://localhost:8000
export PLAYGROUND_SANDBOX_IMAGE=clis-playground-sandbox:local
export PLAYGROUND_SANDBOX_RUNTIME=runc
: "${PLAYGROUND_DEPLOY_TOKEN:?Set and export a local deployment token first}"
uv run --locked --all-packages uvicorn playground.app:create_app --factory \
  --app-dir tools/cli-playground --host 127.0.0.1 --port 8000 \
  --workers 1 --ws-max-size 65536 --no-proxy-headers
```

Keep the gateway running. In another terminal at the repository root, upload the
binary and open the returned `url`. Add more Linux binaries to the `zip` command
to include them in the same shell; each upload replaces the PR's complete batch.

```sh
: "${PLAYGROUND_DEPLOY_TOKEN:?Use the token exported in the gateway terminal}"
sha="$(git rev-parse HEAD)"
zip -q -j - target/playground-linux/catr | curl --fail --silent --show-error \
  -H "Authorization: Bearer $PLAYGROUND_DEPLOY_TOKEN" \
  -H 'Content-Type: application/zip' --data-binary @- \
  "http://localhost:8000/api/environments?sha=$sha&pr=1"
```

For hot reload, restart the gateway with `PLAYGROUND_PUBLIC_URL=http://localhost:5173`,
run `pnpm --dir tools/cli-playground/frontend dev`, and open the preview on port 5173.
Vite proxies API and WebSocket traffic to port 8000. The public URL must match the
browser origin. Otherwise, rebuild the frontend and use port 8000.

## Hosting and checks

Follow the [hosting guide](infra/README.md) for VPS setup, gVisor, secrets and GitHub
Actions. Production requires one gateway worker on a dedicated Docker/gVisor host.
PR links are public; add proxy authentication if needed. PR closure removes the
preview, and new builds replace it at the same URL while ending old sessions.

See [Testing](../../docs/testing.md) for Python, frontend and browser checks,
and the [Linux smoke workflow](../../.github/workflows/cli-playground-sandbox.yml)
for the real Docker/gVisor check.
