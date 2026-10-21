# Develop the playground locally

## Run locally

cli-playground connects to an independently operated private Boxie endpoint. Follow
[local infrastructure](../infra/README.md) and the [Boxie setup](../../../packages/boxie/infra/README.md).
Use native Linux with gVisor for interactive sandboxes. Docker Desktop can build
images and run the product UI against an independently supplied Boxie service,
but does not provide the required sandbox runtime.

```sh
uv sync --all-packages
pnpm --dir tools/cli-playground/frontend install
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
uv run --package cli-playground alembic \
  -c tools/cli-playground/alembic.ini upgrade head
uv run --package cli-playground uvicorn cli_playground.app:create_app \
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

See [persistence](persistence.md) for the synchronous domain, async ports and
PostgreSQL adapters, recovery rules and schema workflow. Metadata belongs to
PostgreSQL; the data directory retains pending upload ZIPs.
