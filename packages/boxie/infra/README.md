# Boxie local infrastructure

Boxie owns its service image, private API, privileged supervisor, artifact data and
trusted profile configuration. Both manifests default to Compose project `boxie`;
set `BOXIE_PROJECT_NAME` in the operator environment file for another installation.
cli-playground uses its own project identity, so either unit can perform orphan cleanup
without owning the other unit's containers. Keep project names distinct, and avoid
a shared `COMPOSE_PROJECT_NAME` or shared `--project-name` override.

The consumer API has no host port. The supervisor is the only Compose service
that mounts the Docker socket; it joins a separate control network. cli-playground
is an optional consumer and is not required to configure or start Boxie.

For production setup, releases and failure inspection, use the
[shared-host guide](../../../infra/environments/single-host/README.md). The
`deploy.sh` script pulls Boxie, migrates and restarts API and supervisor through
Compose. Database, Temporal and monitoring projects remain separate maintenance.
Native host acceptance must pass before manual release deployments can run.

## Configure a disposable native Linux host

Use Docker with the real `runsc` runtime registered. Docker Desktop builds and
configuration checks work, but its `runc` runtime cannot establish gVisor evidence.
Boxie always requests `runsc`; there is no weaker local runtime fallback.

Run from the repository root:

```sh
uv sync --locked --all-packages
docker build -f packages/boxie/infra/Boxie.Dockerfile -t boxie:local .
docker network create --internal boxie-consumers
docker network create --internal boxie-control
```

Choose private operator-owned files outside the repository. Use the names in
[config.example.env](config.example.env) as an environment-file template. Generate
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

Copy [profiles/default.json](profiles/default.json) to the configured profiles
file and replace its placeholder digest with that immutable image ID or the
published registry digest. The default shell, environment and fixtures belong to
Boxie. No consumer sources or profile declaration are required.

The [Temporal deployment](temporal/README.md) is owned here and operated as a
separate Compose project. Its server releases and schema operations remain
independent of Boxie application releases.

Complete the required [host prerequisites](host-requirements.md) first,
including explicit Alembic revisions, namespace setup, and API/supervisor mTLS identities.
Generate both process JSON files (substitute your installation UUID and paths):

```sh
uv run --locked --package boxie-infra python -m boxie_infra.config \
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

## Replace the previous installation

The artifact-only API and fixed default environment require a clean reset of the
previous disposable installation. Follow the
[reset procedure](../../../docs/component-naming-cutover.md) before starting these
services. The older naming converters do not adapt pending requests to this API.
Fresh databases still use the complete Alembic revision chain.

## Validation

Default tests require no Docker or frontend build. Integration checks require
Docker Compose, uv and real built frontend assets; missing tools/assets fail.

```sh
uv run --locked pytest packages/boxie/tests/test_packaging.py
pnpm --dir tools/cli-playground/frontend build
uv run --locked pytest packages/boxie/tests/test_packaging.py -m integration
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
uv run --locked pytest -m integration packages/boxie/tests/test_native_permissions.py
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

## Observability and operator access

[Observability](observability/README.md) and [operator ingress](ingress/README.md)
are Boxie-owned native configuration beside PostgreSQL and Temporal. Their guides
cover the optional network overlays, three shared private bridges, bounded storage
and the small environment substitution step for installation-specific Prometheus
and Access values. Base Boxie and Temporal deployment needs no monitoring inputs.
Only API/supervisor and Temporal join metrics; cli-playground stays on the consumer
network. The minimal collector image supplies the journalctl version required for
cross-boot collection; there are no infrastructure Python renderers.

Service images install only their application package. The root lockfile lists
workspace member manifests, whose metadata is copied for dependency resolution.
