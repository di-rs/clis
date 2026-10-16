# Component releases

`clis-deploy` prepares and activates one source-owned Compose component. It does
not provision infrastructure, publish images, run schema jobs, or make GitHub
calls. Product preparation and session-drain policy remain in the owning service.

Run commands from the repository root using the locked uv workspace:

```sh
uv run --locked python -m deploy_tool validate \
  --descriptor tools/boxie/infra/release-component.json --release release.json
uv run --locked python -m deploy_tool prepare \
  --descriptor tools/boxie/infra/release-component.json --release release.json \
  --environment /private/operator/boxie.environment.json --output /private/operator/prepared
uv run --locked python -m deploy_tool deploy \
  --prepared /private/operator/prepared --profile connection.json \
  --identity /private/operator/deploy-identity --timeout 900
```

Success prints a small JSON result. Invalid inputs and failed activations exit 2;
child output and private input values are never forwarded. An uncertain SSH
result requires inspecting remote component status before retrying.

## Contracts

A source-owned descriptor names only its component's services and files. Paths
are relative to the descriptor directory, preserve their layout on the host, and
cannot traverse symbolic links or parent directories. The Compose document must
contain exactly the declared services. Every running service uses its declared
image variable, with `${IMAGE}`, `${IMAGE:-default}`, or `${IMAGE:?required}`
syntax. Shared networks and volumes must have `external: true` ownership.
Compose `include` and service `extends` are rejected.

```json
{
  "component": "example",
  "compose_file": "compose.production.yml",
  "files": ["routes.yml", "pre-activate.sh"],
  "services": ["api", "worker"],
  "image_variables": {"RUNTIME_IMAGE": ["api", "worker"]},
  "required_environment": ["DATABASE_CREDENTIAL_FILE", "PRIVATE_NETWORK"],
  "healthchecks": {
    "api": {"kind": "docker"},
    "worker": {"kind": "exec", "command": ["python", "-m", "example.health"]}
  },
  "pre_activate": "pre-activate.sh"
}
```

`pre_activate`, `files`, `required_environment`, and `healthchecks` are optional.
Omitted service healthchecks require Docker's `healthy` status. Exec checks run
`docker compose exec -T SERVICE COMMAND...`, after checking the container's image
and Compose ownership. Image variables may map to an empty service list for
immutable component inputs consumed by service-owned preparation.

The release manifest is public, immutable metadata. Revisions are lowercase
40- or 64-character Git object IDs. Every declared image variable must contain a
complete digest reference; tags alone are rejected. Compatibility tokens are
nonempty strings, and `schema` is mandatory.

```json
{
  "component": "example",
  "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "images": {
    "RUNTIME_IMAGE": "registry.example/example@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "compatibility": {"schema": "example-v1", "dependency_api": "v1"}
}
```

`--environment` is a private JSON object of string values. Resolve only the
selected component's dependencies and credentials. Host file references stay
host paths; files explicitly declared by the descriptor are copied into the
release directory. Required values must be nonempty. Immutable image variables
and reserved `CLIS_`, `COMPOSE_`, `DOCKER_`, loader/Python, shell-startup, `HOME`,
and `PATH` variables cannot be overridden. Ambient application environment is
not used for Compose interpolation. The remote adapter inherits only the SSH
user's `PATH` and `HOME`, so preconfigured Docker registry authentication works.

Preparation writes a new directory with mode 0700 and files with mode 0600 from
creation. Its content identity hashes the descriptor, release manifest, file
bytes, and resolved private environment. Changed configuration therefore creates
a different release even at the same Git revision. Deployment verifies this
identity again and requires the prepared environment to remain private. Prepared
bundles contain secrets: do not publish them as CI artifacts or cache entries.

The nonsecret connection profile comes from provisioning:

```json
{
  "host": "192.0.2.10",
  "user": "root",
  "port": 22,
  "host_public_key": "ssh-ed25519 <base64-public-host-key>",
  "identity_ref": "DEPLOY_SSH_KEY"
}
```

`user` and `port` default to `root` and 22. `identity_ref` identifies the deployment
environment's separately stored credential; the operator resolves it and supplies
the private regular file through `--identity` with mode 0600 or stricter. The
profile never contains that private key. SSH ignores user configuration, uses a
private temporary known-hosts file, requires the pinned host key, and disables
agent fallback. Private bundle JSON travels on SSH stdin; no secret enters SSH
or Docker command arguments. Provisioning credentials and Pulumi state access
are unnecessary for an ordinary release. This administrative host credential
still has administrative authority.

## Activation and failure handling

The host needs Python 3.12+, Docker Engine and Compose v2, preexisting external
networks/volumes, component configuration/secrets, and any required schema setup.
Configure private registry pulls on the host separately using a read-only
credential. Do not give the host a CI build/publication token.

A per-host/component file lock rejects concurrent activations. The adapter stores
immutable releases under `/opt/clis/COMPONENT/releases/CONTENT_ID` and always
uses Compose project `clis-COMPONENT`. Compose commands select only descriptor
services, pass an explicit Compose file and project directory, ignore ambient
`.env` files, and activate with `--no-deps --no-build --pull never`. Pulling and
Compose validation happen before the preactivation hook can stop a workload.

The optional hook runs as `sh RELEASE_DIR/SCRIPT` in the candidate release
directory with the resolved component environment and:

- `CLIS_RELEASE_REVISION` and `CLIS_RELEASE_ID` for immutable identity;
- `CLIS_RELEASE_DIR`, `CLIS_COMPOSE_FILE`, and `CLIS_COMPOSE_PROJECT` for Compose;
- `CLIS_PREVIOUS_RELEASE_DIR`, empty on a first installation;
- `CLIS_DEPLOY_DEADLINE_SECONDS`, the remaining activation budget.

The same release identity variables are passed to Compose. A hook must implement
only its owner's policy. Boxie's hook explicitly ends/drains sessions; it does
not promise terminal or process handover. Hook output is suppressed, and nonzero
status prevents candidate startup and attempts compatible previous startup.

Readiness resolves intended image content IDs and checks every running
container's image ID, service/project labels, and Docker or exec readiness.
Service-owned readiness must check actual dependencies and schema compatibility;
a declared manifest token alone cannot establish a live database's schema.

The complete compatibility mapping and service set must match the current
release before any workload mutation. Schema/dependency transitions require an
explicit operator process outside this ordinary application command. This tool
never runs or rolls back database or Temporal schemas.

On success, individually atomic `previous` and `current` symlink replacements
retain the old release. On failed startup/readiness, the tool starts and verifies
the compatible previous release. The candidate is never promoted. With no
previous release, it reports `failed-no-previous`; failed restoration reports
`failed-rollback-failed`. Hook and preflight failures are distinguished. The
private `/opt/clis/COMPONENT/status.json` records the latest outcome; `current`
means the last successfully verified release, not proof that it remains healthy.
An `activating` status after interruption means the result is uncertain.

SSH is bounded to at most 900 seconds, with a 30-second margin for its remote
activation. Individual commands have at most 120 seconds, hooks 90 seconds,
readiness queries 15 seconds, and readiness retries a 120-second window bounded
by the overall deadline. Compatible updates reserve up to 270 seconds/40% of
remaining time for restoration. Command deadlines and a one MiB live stdout limit kill and reap the child process group; noisy hooks cannot fill host temporary storage.
These limits fit a 25-minute deployment job without hiding a failed first
installation or restoration.

Release retention is deliberate: this command never calls Compose `down`,
removes volumes, prunes images, or deletes old releases. Failed candidates and
persistent state remain available for inspection. Storage retention/garbage
collection requires a separate operator policy; application rollback does not
restore filesystems or existing terminal sessions.

## Verification

```sh
uv run --locked pytest tools/deploy/tests
uv run --locked ruff check tools/deploy
uv run --locked ruff format --check tools/deploy
uv run --locked mypy tools/deploy
uv run --locked pytest tools/deploy/tests/test_native_release.py -m integration
```

Portable tests execute the real CLI, pinned-transport boundary, and standalone
remote program using stateful fake SSH/Docker executables. Expected results are
hand-derived from the contracts; there is no independent upstream release tool
or output generator. They cover targeting, immutable identities, permissions,
secret suppression, deadlines, locking, failed first activation, compatible
rollback, failed rollback, and persistent-state retention. They also parse the
shipped remote source using Python 3.12 grammar.

The explicit integration test requires Docker and network access, pulls an
approved official Python tag then pins its returned digest, and exercises real
Compose in randomly named, labeled fixture resources. Selecting it without
prerequisites fails. This supplies local workload activation evidence separately
from fresh cloud bootstrap, gVisor, real product dependencies, SSH host-key
rotation, and production activation gates.
