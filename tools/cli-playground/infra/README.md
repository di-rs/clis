# cli-playground infrastructure

Use the [shared-host guide](../../../infra/environments/single-host/README.md) for
setup, SSH credential handoff, release gates and failure inspection. Routine
backend releases leave the proxy and Boxie alone. A separate sandbox release
updates its approved Boxie profile digest and recreates the supervisor with the
current Boxie image. Existing installations need the
[component naming cutover](../../../docs/component-naming-cutover.md) before their
first release with these names.

## Independent local unit

First obtain a private Boxie URL and consumer credential from its operator; see
[Boxie infrastructure](../../../packages/boxie/infra/README.md). Boxie's approved `cli-playground`
profile must use the immutable product sandbox image, command
`["/opt/cli-playground/shell.sh"]`, environment allowlist `TERM=["xterm-256color"]`, and
artifact mount `/opt/artifact`. The declaration is in
[profiles/cli-playground.json](profiles/cli-playground.json); replace the placeholder digest
and merge it into the **operator-owned Boxie profiles file**. The product cannot
upload or change its profile. Build the product sandbox separately:

```sh
docker build -f tools/cli-playground/Sandbox.Dockerfile -t cli-playground-sandbox:local .
docker build -f tools/cli-playground/infra/Backend.Dockerfile -t cli-playground-backend:local .
```

From the repository root, create an external product-data volume and set the
consumer connection. The network must already exist; it can belong to a separate
Boxie installation. There is no cross-project `depends_on`.

```sh
docker volume create cli-playground-data
export CLI_PLAYGROUND_PROJECT_NAME=cli-playground
export CLI_PLAYGROUND_DATA_VOLUME=cli-playground-data
export CLI_PLAYGROUND_CONSUMER_NETWORK=boxie-consumers
export CLI_PLAYGROUND_BOXIE_URL=http://boxie-api:8090
export CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE=/private/path/consumer.secret
export CLI_PLAYGROUND_DEPLOY_TOKEN=local-deploy-token
export CLI_PLAYGROUND_PUBLIC_URL=http://localhost:8000
docker compose -f tools/cli-playground/infra/compose.yml up --build -d
```

Both local and production manifests default to project `cli-playground`. Override
`CLI_PLAYGROUND_PROJECT_NAME` for another installation; keep it distinct from Boxie's
`BOXIE_PROJECT_NAME` (default `boxie`). Do not give the two units a shared
`COMPOSE_PROJECT_NAME` or `--project-name`, which overrides the manifest identity.
Their external network and volume identifiers remain independently configured.

The credential must be a regular non-symlink file, mode `0600`, containing a
16–4096-character printable token. The local unit publishes only localhost:8000.
The independently resolvable production manifest uses an explicit immutable
`CLI_PLAYGROUND_BACKEND_IMAGE`, external `CLI_PLAYGROUND_PROXY_NETWORK` and
`CLI_PLAYGROUND_CERTIFICATES_VOLUME`; only Traefik publishes 80/443 and receives the
Cloudflare certificate-renewal credential. Boxie's artifact data and supervisor
credential never enter cli-playground. Runtime images contain only their own installed
package/dependencies and, for cli-playground, its real built frontend assets.

Operator modules are installed separately from runtime images. Run
`uv sync --locked --all-packages` for local infrastructure checks. Application
release jobs use native OpenSSH and do not read Pulumi state.

## Enable PR comments and live previews

Benchmark comments need no custom secret. The
[reporting workflow](../../../.github/workflows/cli-bench-report.yml) uses GitHub's
automatic `GITHUB_TOKEN` with `contents: read`, `actions: read` and
`pull-requests: write`. It reports completed benchmark jobs for the PR's current
commit. A run with no selected suites leaves the existing comment unchanged.

For a live cli-playground link, first deploy the backend and its Boxie sandbox using
the [shared-host setup guide](../../../infra/environments/single-host/README.md).
The backend must be reachable from Actions over HTTPS, with working preview DNS
and certificates. In repository **Settings → Environments**, create
`cli-playground-preview` and allow deployments from `master`. Add these settings to
that environment:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `CLI_PLAYGROUND_URL` | The backend's public HTTPS origin, for example `https://play.example.com`, without a path, query or trailing slash. Use the same origin as the backend's `CLI_PLAYGROUND_PUBLIC_URL`. |
| Secret | `CLI_PLAYGROUND_DEPLOY_TOKEN` | The exact token configured as `CLI_PLAYGROUND_DEPLOY_TOKEN` on the backend. Shared-host setup stores it at `/etc/sanctuary/boxie/preview-token`. |

Use the shared-host guide's
[credential handoff](../../../infra/environments/single-host/README.md#give-application-workflows-their-ssh-credentials)
to retrieve the existing token over pinned SSH and copy it into GitHub. Creating
an unrelated token in GitHub will cause authentication to fail. Settings placed
only in the `cli-playground` release environment are unavailable to preview jobs.

If you already have the token in a private local file, set the two values with an
authenticated `gh` session that can manage repository environment settings:

```sh
gh variable set CLI_PLAYGROUND_URL --repo di-rs/sanctuary --env cli-playground-preview \
  --body 'https://play.example.com'
gh secret set CLI_PLAYGROUND_DEPLOY_TOKEN --repo di-rs/sanctuary --env cli-playground-preview \
  < /private/path/preview-token
```

Replace the example origin and file path. The
[preview workflow](../../../.github/workflows/cli-playground-preview.yml) also
uses the automatic GitHub token with `contents: read`, `actions: read`,
`pull-requests: write` and `deployments: write`; no personal access token is needed
by either publisher. PR builds receive neither the preview token nor host SSH
credentials. If the environment requires approval, publication waits for it.

### Verify publication

Merge publisher changes into `master` before triggering new builds: publication
runs trusted default-branch code. Push a CLI change to a ready PR, or add `run-ci`
to a draft as described in [Testing](../../../docs/testing.md#draft-pull-requests).
An ordinary PR description edit does not trigger cli-playground builds.

After the benchmark and cli-playground build workflows complete, check their separate
**CLI benchmark reporting** and **cli-playground PR previews** runs in Actions. The PR
should have a benchmark results comment and a cli-playground comment with a working
preview link. Open the preview and run the changed CLI to verify the sandbox.

If either cli-playground setting is absent, the publisher posts **Configuration
needed** with build artifact links instead of a live preview. A green build alone
does not prove publication succeeded. Check publisher logs for skipped stale
commits, and check the `cli-playground-preview` environment for pending approval or
missing settings. After configuring the environment, rerun the cli-playground binary
build for the current PR commit to trigger publication again.

## Validation

Build the real frontend before `uv run --locked pytest
packages/boxie/tests/test_packaging.py -m integration`. The required PR frontend job
checks isolated packaging and static responses. The separate
[Linux sandbox workflow](../../../.github/workflows/boxie-lifecycle.yml)
checks native runsc security and both generic and product terminals through the
private API. Missing runsc is a failure, never a successful runc substitute.
