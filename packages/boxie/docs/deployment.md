# Deploy Boxie

Boxie deployment files live in [infra/](../infra/). They build and deploy the API,
supervisor, and sandbox image, and configure their supporting services.

## Start here

For the shared production host, follow [host setup and releases](../../../infra/README.md).
For a disposable native Linux machine, follow [local setup](#configure-a-disposable-native-linux-host).
Both require the [host prerequisites](deployment.md#deployment-prerequisites), including gVisor,
database migrations, Temporal setup, and private service credentials.

Docker Desktop is useful for image builds and configuration checks. Running a
sandbox requires `runsc`; Boxie does not fall back to a weaker runtime.

## Deployment boundaries

The API accepts private consumer traffic. The supervisor alone receives Docker
access and artifact storage. The playground is an optional, separate consumer.
Keep its Compose project name distinct from Boxie's `BOXIE_PROJECT_NAME`
(default `boxie`); a shared project-name override can make cleanup affect both.

Database, Temporal, and monitoring projects have separate maintenance lifecycles.
An ordinary Boxie release migrates its database schema and restarts the API and
supervisor without upgrading those services.

| Component | Setup and operations |
| --- | --- |
| PostgreSQL | [Databases, migrations, and retention](database.md) |
| Temporal | [Certificates, namespace, and schema operations](temporal.md) |
| Monitoring | [Logs, metrics, and storage budgets](observability.md) |
| Operator access | [Protected Grafana and Temporal dashboards](#operator-dashboard-access) |

Use the [validation checks](#validation) to distinguish packaging checks from
native Linux isolation and lifecycle checks. A successful image build does not
prove the deployment is ready.

## Deployment prerequisites

Boxie uses independently operated [PostgreSQL](database.md)
and production [Temporal](temporal.md) on the private control
network. Apply their explicit schema and namespace jobs first. Production never
uses a development/test Temporal server. Temporal certificate holders are trusted
cluster administrators; a namespace is organizational, not an RBAC boundary.

The two Boxie processes are `boxie-api` and `boxie-supervisor`. The supervisor
embeds Temporal SDK workers; there is no standalone worker service or worker
configuration. Generate `api.json` and `supervisor.json` with `boxie_infra.config`
using [config.example.env](../infra/config.example.env). Both receive distinct mTLS client
identities. Only the supervisor receives Docker access and artifact storage.

Apply [database migrations](database.md#apply-boxie-migrations) before starting
Boxie. Production activation also requires a demonstrated
[backup restore](database.md#recovery-prerequisite-for-production-activation).
No direct-mode data directory upgrade or PTY restart recovery is supported.

## Configure a disposable native Linux host

Use Docker with the real `runsc` runtime registered. Docker Desktop builds and
configuration checks work, but its `runc` runtime cannot establish gVisor evidence.
Boxie always requests `runsc`; there is no weaker local runtime fallback.

Run from the repository root:

```sh
uv sync --all-packages
docker build -f packages/boxie/infra/Boxie.Dockerfile -t boxie:local .
docker network create --internal boxie-consumers
docker network create --internal boxie-control
```

Choose private operator-owned files outside the repository. Use the names in
[config.example.env](../infra/config.example.env) as an environment-file template. Generate
two different random printable credentials (at least 16 characters), stored as
regular files with mode `0600`: one supervisor credential and one consumer credential.
Do not put credentials in URLs. The API reads both files; the supervisor only reads its
own credential. A consumer receives only its own credential.

Linux bind mounts preserve numeric ownership. The current Boxie image
runs as UID 0; with `cap_drop: ALL`, it cannot bypass another UID's file
permissions. Provision its supervisor/consumer credentials, database DSN and dedicated
Temporal private keys as reader-owned regular files with mode `0600` (UID 0 for
the current production manifests). Keep secret mounts read-only. Public
certificates and configuration must also be readable by their service UID, and
each containing directory must permit traversal. Create `BOXIE_DATA_DIR` and
its `profiles` subdirectory with the supervisor's UID and owner write/traverse access
before starting Compose; Docker-created bind-source directories can have the
wrong owner. The pinned Temporal server, operator and UI images use UID 1000;
their private keys must be owned by that reader with mode `0600` as well.
Do not compensate with group/world access or additional DAC capabilities.

The shared-host installer provisions reader ownership and consumer credentials.
Key rotation remains operator maintenance. The configuration generator writes
paths only; it does not provision ownership.

Build the Boxie sandbox image from the repository root:

```sh
docker build -f packages/boxie/sandbox/Dockerfile -t boxie-sandbox:local .
docker image inspect boxie-sandbox:local --format '{{.Id}}'
```

Copy [profiles/default.json](../infra/profiles/default.json) to the configured profiles
file and replace its placeholder digest with that immutable image ID or the
published registry digest. The default shell, environment and fixtures belong to
Boxie. No consumer sources or profile declaration are required.

The [Temporal deployment](temporal.md) is owned here and operated as a
separate Compose project. Its server releases and schema operations remain
independent of Boxie application releases.

Complete the required [host prerequisites](deployment.md#deployment-prerequisites) first,
including explicit Alembic revisions, namespace setup, and API/supervisor mTLS identities.
Generate both process JSON files (substitute your installation UUID and paths):

```sh
uv run --package boxie-infra python -m boxie_infra.config \
  --output /etc/boxie --data-dir /var/lib/boxie \
  --installation-id 00000000-0000-0000-0000-000000000001 \
  --host-id local-linux --consumer-id example-consumer

docker compose --env-file /etc/boxie/local.env \
  -f packages/boxie/infra/compose.yml up --build -d
```

The configuration generator contains no secret values. `--data-dir` must equal
`BOXIE_DATA_DIR`: this absolute directory is mounted at the **identical path** in
the supervisor. Docker resolves child artifact bind sources on its daemon host, so a
container-only path or a client path on a remote Docker host is invalid. Storage
is an explicit host bind, not a volume whose opaque mountpoint must be guessed.
The API receives no artifact storage. Both networks are externally supplied and
owned by the operator, never by either Compose project. Choose a unique API alias
when operating multiple installations.

## Operator dashboard access

This tunnel gives operators access to Grafana and Temporal UI through Cloudflare
Access. Each dashboard has its own hostname, Access application, and audience ID.
The dashboards stay on private networks; the tunnel does not expose Boxie's API,
metrics, or log ingestion. Unknown hostnames return 404.

### Prepare access

Create the two self-hosted Access applications and a locally managed tunnel in
Cloudflare. Record the team name, distinct application audiences and tunnel UUID.
Create DNS tunnel routes for the two dashboard hostnames. Keep the tunnel JSON
credentials outside the checkout in a private file readable by UID 65532. Grafana
also needs its own admin secret, configured by the monitoring deployment.

### Configure the tunnel

The config template takes six nonsecret values. Export the
following values, using exact hostnames and separate nonempty audience IDs:

```sh
export OPERATOR_TUNNEL_ID=00000000-0000-0000-0000-000000000001
export OPERATOR_TEAM_NAME=operators
export OPERATOR_GRAFANA_HOSTNAME=grafana.example.com
export OPERATOR_TEMPORAL_HOSTNAME=temporal.example.com
export OPERATOR_GRAFANA_AUDIENCE=replace-with-grafana-application-audience
export OPERATOR_TEMPORAL_AUDIENCE=replace-with-temporal-application-audience
export OPERATOR_CONFIG_FILE=/private/boxie/cloudflared.yml
export OPERATOR_CREDENTIALS_FILE=/private/boxie/tunnel.json
: "${OPERATOR_GRAFANA_AUDIENCE:?}" "${OPERATOR_TEMPORAL_AUDIENCE:?}"
test "$OPERATOR_GRAFANA_AUDIENCE" != "$OPERATOR_TEMPORAL_AUDIENCE"
envsubst '${OPERATOR_TUNNEL_ID} ${OPERATOR_TEAM_NAME} ${OPERATOR_GRAFANA_HOSTNAME} ${OPERATOR_TEMPORAL_HOSTNAME} ${OPERATOR_GRAFANA_AUDIENCE} ${OPERATOR_TEMPORAL_AUDIENCE}' \
  < packages/boxie/infra/ingress/cloudflared.yml > "$OPERATOR_CONFIG_FILE"
docker compose -f packages/boxie/infra/ingress/compose.yml \
  run --rm cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
docker compose -f packages/boxie/infra/ingress/compose.yml up -d
```

The [monitoring setup](observability.md) creates the three shared private
bridges. Its optional Temporal overlay attaches the UI to the tunnel bridge.
Grafana's root URL must be `https://` plus the same Grafana hostname; secure
cookies and local Grafana login remain enabled behind Access. No credentials are
embedded in YAML. Changing Access policies belongs to the Cloudflare operator;
cloudflared validates the Access tokens.

### Verify access

Test both origins with unauthenticated, wrong-audience and valid
operator requests through the real tunnel. Also test an unconfigured hostname
and verify direct origins are unreachable externally. The local native ingress
validator checks configuration shape; it does not prove live Access authorization.

## Validation

Default tests require no Docker or frontend build. Integration checks require
Docker Compose, uv and real built frontend assets; missing tools/assets fail.

```sh
uv run pytest packages/boxie/tests/test_packaging.py
pnpm --dir tools/cli-playground/frontend build
uv run pytest packages/boxie/tests/test_packaging.py -m integration
```

The optional native Linux [Boxie check](../../../.github/workflows/boxie-lifecycle.yml)
starts PostgreSQL, Temporal, API and Supervisor without cli-playground. It checks
process faults, the generic consumer API and container cleanup. The separate
[shared-system check](../../../.github/workflows/sanctuary-lifecycle.yml) adds the
application and browser to verify native CLIs, fixtures, pipes, resize and reconnect.
Both require real Linux amd64/runsc; image builds and fake-daemon tests do not
establish native isolation. Native tests remain manual while their hang is investigated.
PRs retain SQL/Temporal, credential permission and endpoint restart checks.

The lifecycle fixture runs API and supervisor as the runner's numeric UID:GID
so its private files and writable storage share one owner. Its Linux-volume DAC
regression resolves the actual test Compose mounts and checks readable private
credentials, immutable secret mounts, writable data/profiles and rejected unsafe
credentials. Run it with the freshly built Boxie image:

```sh
uv run pytest -m integration packages/boxie/tests/test_native_permissions.py
```

This probe can verify Linux ownership on an arm64 Docker host, but only the native
amd64/runsc lifecycle workflow proves the full lifecycle. The existing disposable
Temporal server/operator/UI fixture keys remain mode `0644` to support their
unchanged UID-1000 images and host-side test readers; this is a fixture limitation,
not the production provisioning policy. API/supervisor and host-only client fixture
keys are mode `0600`.

The native lifecycle fixture publishes the API on the test runner's loopback
interface. Its operator process runs `boxie_infra.smoke` there with Docker CLI
access to inspect sandbox security and deletion. Run the linked lifecycle
workflow with `workflow_dispatch` for this check. Production does not publish an
API host port, so the fixture's smoke command does not target a deployed host.
cli-playground never receives Docker access.
