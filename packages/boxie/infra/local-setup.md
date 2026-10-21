# Set up Boxie on a Linux host

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
