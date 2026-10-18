# Boxie local infrastructure

Boxie owns its runtime image, private API, privileged orchestrator, artifact data and
trusted profile configuration. Both manifests default to Compose project `boxie`;
set `BOXIE_PROJECT_NAME` in the operator environment file for another installation.
Playground uses its own project identity, so either unit can perform orphan cleanup
without owning the other unit's containers. Keep project names distinct, and avoid
a shared `COMPOSE_PROJECT_NAME` or shared `--project-name` override.

The consumer API has no host port. The orchestrator is the only Compose service
that mounts the Docker socket; it joins a separate control network. Playground
is an optional consumer and is not required to configure or start Boxie.

For production setup, releases and failure inspection, use the
[shared-host guide](../../../infra/environments/single-host/README.md). The
`deploy.sh` script pulls Boxie, migrates and restarts API and orchestrator through
Compose. Database, Temporal and monitoring projects remain separate maintenance.
Native host acceptance and the off-host backup/restore gate must pass before
automatic releases are enabled.

## Configure a disposable native Linux host

Use Docker with the real `runsc` runtime registered. Docker Desktop builds and
configuration checks work, but its `runc` runtime cannot establish gVisor evidence.
Boxie always requests `runsc`; there is no weaker local runtime fallback.

Run from the repository root:

```sh
uv sync --locked --all-packages
docker build -f tools/boxie/infra/Runtime.Dockerfile -t boxie:local .
docker network create --internal boxie-consumers
docker network create --internal boxie-control
```

Choose private operator-owned files outside the repository. Use the names in
[config.example.env](config.example.env) as an environment-file template. Generate
two different random printable credentials (at least 16 characters), stored as
regular files with mode `0600`: one orchestrator credential and one consumer credential.
Do not put credentials in URLs. The API reads both files; the orchestrator only reads its
own credential. A consumer receives only its own credential.

Linux bind mounts preserve numeric ownership. The current Boxie runtime image
runs as UID 0; with `cap_drop: ALL`, it cannot bypass another UID's file
permissions. Provision its orchestrator/consumer credentials, database DSN and dedicated
Temporal private keys as reader-owned regular files with mode `0600` (UID 0 for
the current production manifests). Keep secret mounts read-only. Public
certificates and configuration must also be readable by their service UID, and
each containing directory must permit traversal. Create `BOXIE_DATA_DIR` and
its `profiles` subdirectory with the orchestrator's UID and owner write/traverse access
before starting Compose; Docker-created bind-source directories can have the
wrong owner. The pinned Temporal server, operator and UI images use UID 1000;
their private keys must be owned by that reader with mode `0600` as well.
Do not compensate with group/world access or additional DAC capabilities.

The shared-host installer provisions reader ownership and consumer credentials.
Key rotation remains operator maintenance. The configuration generator writes
paths only; it does not provision ownership.

Copy [profiles.example.json](profiles.example.json) to the configured profiles
file, replacing its placeholder digest with the immutable Docker image ID or
registry digest for an approved shell image. For example, after pulling
`busybox:1.37.0`, `docker image inspect busybox:1.37.0 --format '{{.Id}}'` supplies
its local immutable ID. The `smoke` profile approves `/bin/sh` and a generic text
artifact; it has no PR, hostname or executable-format assumptions. Product
operators may separately approve the
[Playground profile](../../cli-playground/infra/profiles/playground.json) with the
immutable product sandbox image digest. Profiles are never consumer uploads.

The [Temporal deployment](temporal/README.md) is owned here and operated as a
separate Compose project. Its server releases and schema operations remain
independent of Boxie application releases.

Complete the required [runtime prerequisites](runtime.md) first,
including explicit Alembic revisions, namespace setup, and API/orchestrator mTLS identities.
Generate all three process JSON files (substitute your installation UUID and paths):

```sh
uv run --locked python -m boxie_infra.config \
  --output /etc/boxie --data-dir /var/lib/boxie \
  --installation-id 00000000-0000-0000-0000-000000000001 \
  --host-id local-linux --consumer-id playground

docker compose --env-file /etc/boxie/local.env \
  -f tools/boxie/infra/compose.yml up --build -d
```

The configuration generator contains no secret values. `--data-dir` must equal
`BOXIE_DATA_DIR`: this absolute directory is mounted at the **identical path** in
the orchestrator. Docker resolves child artifact bind sources on its daemon host, so a
container-only path or a client path on a remote Docker host is invalid. Storage
is an explicit host bind, not a volume whose opaque mountpoint must be guessed.
The API receives no artifact storage. Both networks are externally supplied and
owned by the operator, never by either Compose project. Choose a unique API alias
when operating multiple installations.

## Validation

Default tests require no Docker or frontend build. Integration checks require
Docker Compose, uv and real built frontend assets; missing tools/assets fail.

```sh
uv run --locked pytest tools/boxie/tests/test_packaging.py
pnpm --dir tools/cli-playground/frontend build
uv run --locked pytest tools/boxie/tests/test_packaging.py -m integration
```

The optional native Linux [sandbox check](../../../.github/workflows/boxie-lifecycle.yml)
starts isolated PostgreSQL, Temporal, API, orchestrator and Playground services.
Manual `workflow_dispatch` runs exercise browser recovery, process faults and both
`boxie_infra.smoke` and `playground_infra.smoke` through HTTP and WebSocket. Each
inspects the actual sandbox's `runsc`, non-root user, no-network mode, read-only
root/artifact, capabilities, no-new-privileges, resource limits, tmpfs, disabled
Docker logs and confirmed container deletion. The product smoke also checks the
native Rust CLI, fixtures, pipes/redirection, resize and Ctrl-C. Ordinary Docker
image builds or fake-daemon E2Es do not count as this platform check. All six native
lifecycle/browser cases are temporarily excluded from automatic PR runs while
their hang is investigated. PRs retain real SQL/Temporal integration, credential
permission and endpoint restart checks.

The lifecycle fixture runs API and orchestrator as the runner's numeric UID:GID
so its private files and writable storage share one owner. Its Linux-volume DAC
regression resolves the actual test Compose mounts and checks readable private
credentials, immutable secret mounts, writable data/profiles and rejected unsafe
credentials. Run it with the freshly built runtime image:

```sh
uv run --locked pytest -m integration tools/boxie/tests/test_native_permissions.py
```

This probe can verify Linux ownership on an arm64 Docker host, but only the native
amd64/runsc lifecycle workflow proves the full lifecycle. The existing disposable
Temporal server/operator/UI fixture keys remain mode `0644` to support their
unchanged UID-1000 images and host-side test readers; this is a fixture limitation,
not the production provisioning policy. API/orchestrator and host-only client fixture
keys are mode `0600`.

The native lifecycle fixture publishes the API on the test runner's loopback
interface. Its operator process runs `boxie_infra.smoke` there with Docker CLI
access to inspect sandbox security and deletion. Run the linked lifecycle
workflow with `workflow_dispatch` for this check. Production does not publish an
API host port, so the fixture's smoke command does not target a deployed host.
Playground never receives Docker access.

## Observability and operator access

[Observability](observability/README.md) and [operator ingress](ingress/README.md)
are Boxie-owned native configuration beside PostgreSQL and Temporal. Their guides
cover the optional network overlays, three shared private bridges, bounded storage
and the small environment substitution step for installation-specific Prometheus
and Access values. Base Boxie and Temporal deployment needs no monitoring inputs.
Only API/orchestrator and Temporal join metrics; Playground stays on the consumer
network. The minimal collector image supplies the journalctl version required for
cross-boot collection; there are no infrastructure Python renderers.

Runtime images install only their application package. The root lockfile lists
workspace member manifests, whose metadata is copied for dependency resolution.
