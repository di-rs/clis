# Playground infrastructure

Use the [shared-host guide](../../../infra/environments/single-host/README.md) for
setup, SSH credential handoff, release gates and failure inspection. Routine
gateway releases leave the proxy and Boxie alone. A separate sandbox release
updates its approved Boxie profile digest and recreates the orchestrator with the
current Boxie image.

## Independent local unit

First obtain a private Boxie URL and consumer credential from its operator; see
[Boxie infrastructure](../../../packages/boxie/infra/README.md). Boxie's approved `playground`
profile must use the immutable product sandbox image, command
`["/opt/playground/shell.sh"]`, environment allowlist `TERM=["xterm-256color"]`, and
artifact mount `/opt/artifact`. The declaration is in
[profiles/playground.json](profiles/playground.json); replace the placeholder digest
and merge it into the **operator-owned Boxie profiles file**. The product cannot
upload or change its profile. Build the product sandbox separately:

```sh
docker build -f tools/cli-playground/Sandbox.Dockerfile -t sanctuary-playground-sandbox:local .
docker build -f tools/cli-playground/infra/Playground.Dockerfile -t cli-playground:local .
```

From the repository root, create an external product-data volume and set the
consumer connection. The network must already exist; it can belong to a separate
Boxie installation. There is no cross-project `depends_on`.

```sh
docker volume create playground-data
export PLAYGROUND_PROJECT_NAME=cli-playground
export PLAYGROUND_DATA_VOLUME=playground-data
export PLAYGROUND_CONSUMER_NETWORK=boxie-consumers
export PLAYGROUND_BOXIE_URL=http://boxie-api:8090
export PLAYGROUND_BOXIE_CREDENTIAL_FILE=/private/path/consumer.secret
export PLAYGROUND_DEPLOY_TOKEN=local-deploy-token
export PLAYGROUND_PUBLIC_URL=http://localhost:8000
docker compose -f tools/cli-playground/infra/compose.yml up --build -d
```

Both local and production manifests default to project `cli-playground`. Override
`PLAYGROUND_PROJECT_NAME` for another installation; keep it distinct from Boxie's
`BOXIE_PROJECT_NAME` (default `boxie`). Do not give the two units a shared
`COMPOSE_PROJECT_NAME` or `--project-name`, which overrides the manifest identity.
Their external network and volume identifiers remain independently configured.

The credential must be a regular non-symlink file, mode `0600`, containing a
16–4096-character printable token. The local unit publishes only localhost:8000.
The independently resolvable production manifest uses an explicit immutable
`PLAYGROUND_IMAGE`, external `PLAYGROUND_PROXY_NETWORK` and
`PLAYGROUND_CERTIFICATES_VOLUME`; only Traefik publishes 80/443 and receives the
Cloudflare certificate-renewal credential. Boxie's artifact data and orchestrator
credential never enter Playground. Runtime images contain only their own installed
package/dependencies and, for Playground, its real built frontend assets.

Operator modules are installed separately from runtime images. Run
`uv sync --locked --all-packages` for local infrastructure checks. Application
release jobs use native OpenSSH and do not read Pulumi state.

The preview controller keeps its existing HTTP publication API, stable PR URLs,
current-head checks and close/reopen handling. Its URL and token belong in the
`playground-preview` GitHub environment. PR builds receive neither that token nor
host deployment credentials.

## Validation

Build the real frontend before `uv run --locked pytest
packages/boxie/tests/test_packaging.py -m integration`. The required PR frontend job
checks isolated packaging and static responses. The separate
[Linux sandbox workflow](../../../.github/workflows/boxie-lifecycle.yml)
checks native runsc security and both generic and product terminals through the
private API. Missing runsc is a failure, never a successful runc substitute.
