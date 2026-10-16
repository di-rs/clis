# Boxie local infrastructure

Boxie owns its runtime image, private API, privileged orchestrator, artifact data and
trusted profile configuration. Both manifests default to Compose project `boxie`;
set `BOXIE_PROJECT_NAME` in the operator environment file for another installation.
Playground uses its own project identity, so either unit can perform orphan cleanup
without owning the other unit's containers. Keep project names distinct, and avoid
a shared `COMPOSE_PROJECT_NAME` or shared `--project-name` override.

The consumer API has no host port. Only the orchestrator mounts the Docker socket; it joins a separate control network. Playground is an
optional consumer and is not required to configure or start Boxie.

Production activation is unavailable during PR1–PR3. The independently targetable
`compose.production.yml` describes the private topology for validation; PR4 will
supply split-service release and activation tooling. No command here provisions
cloud resources.

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

Copy [profiles.example.json](profiles.example.json) to the configured profiles
file, replacing its placeholder digest with the immutable Docker image ID or
registry digest for an approved shell image. For example, after pulling
`busybox:1.37.0`, `docker image inspect busybox:1.37.0 --format '{{.Id}}'` supplies
its local immutable ID. The `smoke` profile approves `/bin/sh` and a generic text
artifact; it has no PR, hostname or executable-format assumptions. Product
operators may separately approve the
[Playground profile](../../cli-playground/infra/profiles/playground.json) with the
immutable product sandbox image digest. Profiles are never consumer uploads.

Generate the process JSON files (substitute your installation UUID and paths):

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

The native Linux [sandbox check](../../../.github/workflows/cli-playground-sandbox.yml)
runs [smoke.sh](smoke.sh), which starts separate API and orchestrator processes
bound to loopback, then exercises both
`boxie_infra.smoke` and `playground_infra.smoke` through HTTP and WebSocket. Each
inspects the actual sandbox's `runsc`, non-root user, no-network mode, read-only
root/artifact, capabilities, no-new-privileges, resource limits, tmpfs, disabled
Docker logs and confirmed container deletion. The product smoke also checks the
native Rust CLI, fixtures, pipes/redirection, resize and Ctrl-C. Ordinary Docker
image builds or fake-daemon E2Es do not count as this platform check.

A consumer-network operator can run `python -m boxie_infra.smoke --boxie-url
http://boxie-api:8090 --credential-file /private/consumer.secret` with the `smoke`
profile approved. The inspection portion also requires operator Docker access;
that access belongs to the test runner, never the Playground service.
