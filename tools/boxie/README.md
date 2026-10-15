# Boxie sandbox service

Boxie is an independently deployed, private sandbox service. The privileged agent
owns Docker access, immutable generic artifacts, admission, idempotency, capacity,
and terminal lifetimes. Consumers select approved profiles and upload ZIP files;
they cannot supply images, host paths, Docker options, or isolation settings.
This package does not depend on a consuming product or inspect executable formats.

The unprivileged consumer API delegates admission and terminal ownership to the
agent over its authenticated private protocol. PR1 uses a transitional direct
lifecycle with agent-local admission and recovery. PR2 replaces that authority
with PostgreSQL and Temporal after PR1's required checks pass, including native
Linux amd64/runsc. PR1 does not provide durable workflow orchestration or terminal
replay. Run exactly one agent process against its local data directory.
The directory and profile files must be writable only by the operator/agent. The
Docker daemon must see artifact paths at the same absolute location as the agent.

## Run

Use the root workspace and lockfile:

```sh
uv sync --locked --all-packages
uv run --locked --package boxie boxie-agent --config /etc/boxie/agent.json
```

An operator configuration file contains:

```json
{
  "data_dir": "/var/lib/boxie",
  "profiles_file": "/etc/boxie/profiles.json",
  "credential_file": "/run/secrets/boxie-agent",
  "identity": {
    "installation_id": "b18283b1-dc32-46e7-8da5-7aa380043685",
    "host_id": "sandbox-1"
  },
  "bind_host": "127.0.0.1",
  "bind_port": 8091
}
```

Generate a unique installation UUID once and preserve it with the data directory.
The credential is a separate agent-control secret, at least 16 printable ASCII
bytes, in a private regular file (mode `0600`). Consumer API credentials must not
be reused here. Only trusted services on a private network may reach the agent;
there is no public endpoint. Access logging is disabled by the entry point and
credentials are never accepted in URLs.

Run the separate consumer API without a Docker socket or artifact host mounts:

```sh
uv run --locked --package boxie boxie-api --config /etc/boxie/api.json
```

Its operator configuration contains:

```json
{
  "agent_url": "http://boxie-agent:8091",
  "agent_credential_file": "/run/secrets/boxie-agent",
  "consumer_credential_files": {
    "playground": "/run/secrets/boxie-playground"
  },
  "release": "boxie-pr1",
  "request_timeout": 20,
  "bind_host": "127.0.0.1",
  "bind_port": 8090
}
```

Each credential is a private regular file with the same restrictions as the agent
secret. Consumer credentials must be unique and distinct from the agent secret.
Only composition roots read secrets; transports receive injected credentials.
The API resolves consumer identity from Bearer authentication and never accepts
consumer identity in a sandbox request body. Neither API nor agent is public.
HTTP and WebSocket redirects are disabled for agent calls. HTTP control bodies
and responses are bounded to 64 KiB; uploads stream in chunks of at most 64 KiB
with an actual-byte ceiling of 512 MiB and a configured total request deadline.

## Consumer API

Every resource request and terminal handshake requires a consumer Bearer secret.
Foreign resources return 404. UUIDs and digests remain opaque to the API.

| Operation | Request and response |
| --- | --- |
| Upload artifact | `PUT /v1/artifacts/{uuid}`, raw ZIP, `X-Content-SHA256`; 201 new, 200 exact retry |
| Retire artifact | `DELETE /v1/artifacts/{uuid}`; 204 |
| Create sandbox | `POST /v1/sandboxes`, `Idempotency-Key`, `spec` object described below; 202 with status |
| Inspect sandbox | `GET /v1/sandboxes/{uuid}`; 200 with status |
| Stop sandbox | `DELETE /v1/sandboxes/{uuid}`; 200 with status |
| Terminal | `WS /v1/sandboxes/{uuid}/terminal` |

Sandbox POST sends the `spec` object directly, without the agent's ownership or
idempotency envelope. Unknown fields, images, paths and Docker settings are
rejected. Invalid bodies return 422, conflicting retries 409, capacity exhaustion
429 and unavailable agent calls 503. API restarts retain agent-owned IDs,
idempotency records, capacity and terminal draining.

Consumer terminal output is binary. Input is a JSON text frame
`{"type":"input","data":"echo hello\n"}`; resize uses
`{"type":"resize","columns":80,"rows":24}`. Control frames are at most 16 KiB
of UTF-8 including JSON, columns are 1–500 and rows 1–300. Unicode survives
fragmented WebSocket messages and output chunk boundaries. JSON status frames
contain `type: status` and a termination `reason`. A disconnect keeps the agent
draining during its two-minute grace. A consumer should offer a fresh session;
reattachment and output replay arrive in the next increment.

`GET /healthz` is unauthenticated liveness. Authenticated `GET /readyz` returns
the API release and required agent dependency result, with 503 when unavailable.
The agent's authenticated `/readyz` reports its identity and a bounded Docker
dependency check. Access logs are disabled by both entry points.

A trusted profile file is a JSON object keyed by profile ID:

```json
{
  "shell": {
    "image_digest": "registry.example/shell@sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "commands": [["/bin/sh"]],
    "env": {"TERM": ["xterm-256color"]},
    "artifact_target": "/opt/artifact"
  }
}
```

Replace the example image digest with an approved image. Local content-addressed
image IDs (`sha256:` plus 64 lowercase hex digits) are also accepted. Commands
are exact argument-vector allowlists; environment keys and values are allowlists.
The artifact mount must be a normalized path under `/opt/`. Optional `limits`
may lower the defaults but cannot increase them. Send `SIGHUP` to validate and
atomically reload all profiles. A failed reload keeps the last valid version.
Approved versions are retained by configuration hash, including across agent
restarts, so delayed requests can validate their original snapshot. Reloads never
change an admitted sandbox's image, command, environment, or limits.

## Private protocol

Every HTTP request and WebSocket handshake requires `Authorization: Bearer SECRET`.
The agent uses constant-time credential comparison. A trusted API supplies the
consumer identity; resources belonging to another consumer return 404. UUIDs are
opaque resource IDs. Wire schemas reject extra object fields and invalid bounds.

| Operation | Request |
| --- | --- |
| Upload artifact | `PUT /v1/artifacts/{uuid}?consumer_id=...`, raw ZIP body, `X-Content-SHA256` header |
| Retire artifact | `DELETE /v1/artifacts/{uuid}?consumer_id=...` |
| Resolve profile | `POST /v1/profiles/resolve`, `{profile_id, command, env}` |
| Admit sandbox | `POST /v1/sandboxes`, `{consumer_id, idempotency_key, spec}` |
| Inspect sandbox | `GET /v1/sandboxes/{uuid}?consumer_id=...` |
| Terminate sandbox | `DELETE /v1/sandboxes/{uuid}?consumer_id=...&reason=requested` |
| Terminal | `WS /v1/sandboxes/{uuid}/terminal?consumer_id=...` |
| Readiness | `GET /readyz`, agent identity and dependency results |

`spec` contains `artifact_id`, `profile_id`, `command`, `env`, and optional `policy`.
Policy defaults are `max_seconds: 3600`, `idle_seconds: 600`, and
`disconnect_seconds: 120`; each may be lowered. Admission returns a `SandboxView`
with `id`, `state`, `created_at`, `absolute_deadline`, `reason`, and
`cleanup_pending`. A repeated consumer/idempotency key returns the same ID only
for the exact canonical specification; changed requests return 409. Capacity is
eight sandboxes by default, including uncertain creation and pending deletion.

Lower-level `POST /v1/sandboxes/{uuid}/{ensure,inspect,remove}` operations require
`consumer_id`, `identity: {installation_id, host_id}`, and `generation`. Ensure
also requires the original `snapshot`, `artifact_id`, and `deadline`; remove
accepts a termination `reason`. **PR1 ensure only operates on an already-admitted
persisted record.** It cannot admit a new resource or bypass admission ownership,
policy, capacity, or idempotency. The future database authority will supply a
complete trusted provisioning request instead.

Terminal input and output use binary WebSocket frames without Unicode decoding.
Handshake denials use bounded HTTP JSON responses: 401 for invalid agent
credentials, 404 for unavailable ownership, and 409 for attachment conflicts.
The API maps failed internal authentication or unsupported denial codes to 503;
consumer authentication is resolved before any agent request.
Input frames are at most 64 KiB. Resize messages are JSON text frames:
`{"type":"resize","columns":80,"rows":24}`; columns are 1–500 and rows 1–300.
Termination status is a JSON text frame with `type: status` and a `reason`, distinct
from terminal bytes. One terminal attachment is allowed; page reload recovery and
output replay are outside this increment. The agent attaches to Docker before
starting the container so initial output is retained even without Docker logs.

## Isolation and lifetime

Containers run as `65534:65534` under gVisor (`runsc`), with a read-only root,
no network, all capabilities dropped, and `no-new-privileges`. Limits are 512 MiB
RAM (including swap allowance), one CPU, 256 PIDs, nofile 1024, a 64 MiB `/workspace`
and 16 MiB `/tmp` tmpfs, and 16 MiB total terminal output. The tmpfs mounts use
`nosuid,nodev,noexec`. Artifacts are mounted read-only. The admitted command
replaces the image entrypoint.

Absolute lifetime begins at accepted creation, including provisioning. Idle time
begins when ready and resets only after nonempty input is accepted by Docker.
Resize, output, reads, and reconnect attempts do not reset it. Disconnection
starts a separate two-minute deadline. Cleanup operations are bounded and run
independently so an unresponsive container does not block other deadlines.
Terminals close when termination begins; slots and artifacts remain reserved
until Docker deletion is confirmed. The agent survives consumer API restarts;
an agent restart may end terminals.

Local intent records are durably replaced before Docker creation. Deterministic
names and installation, host, sandbox, generation, consumer, artifact, and deadline
labels allow restart cleanup of precisely owned resources. Startup never performs
a daemon-wide delete. A name occupied by different labels is retained for operator
inspection, never deleted. If a create call times out and no container is yet
observable, absence alone cannot prove that a late create will not occur: cleanup
stays pending and retains capacity/artifacts until the owned container is observed
and removed. This conservative uncertainty can require operator investigation.

## Artifacts

ZIP staging hashes streamed compressed bytes, checks the supplied SHA-256, and
extracts in bounded chunks into an unpublished directory. Limits are 512 MiB
compressed, 512 MiB actual expanded bytes, 64 MiB per member, and 1024 regular
files. Before constructing the ZIP parser, a bounded preflight caps the central
directory at 4 MiB and scans its actual entries (including directories), with a
ceiling of 2048. Advertised counts cannot bypass this scan. Classic ZIP and bounded
ZIP64 metadata are accepted. Absolute paths, traversal,
backslashes, duplicate normalized names, links/devices, encrypted entries, and
unsupported compression are rejected. ZIP stored and deflate compression are
accepted. Files are ordinary generic data; nested text and executable files are
both supported. Files receive mode `0555`, removing unsafe permission bits;
directories use ordinary traversable permissions. Publication uses atomic rename
and metadata replacement. Cancellation removes staging; startup removes abandoned
unpublished staging directories.

An artifact UUID and digest are immutable. Repeating the same upload is idempotent;
a different digest returns 409. Both private HTTP APIs return 201 for new
publication and 200 for an exact retry, using an explicit typed application
publication result. Retirement is durable and prevents new admission.
Physical files remain while a sandbox reference exists, including uncertain
creation or failed deletion. Metadata tombstones preserve UUID immutability after
collection. No consumer can select a host filesystem path.

## Verification and feasibility

```sh
uv run --locked pytest tools/boxie/tests/test_agent.py tools/boxie/tests/test_artifacts.py tools/boxie/tests/test_profiles.py
uv run --locked pytest tools/boxie/tests/test_api.py tools/boxie/tests/test_service_boundary.py tools/boxie/tests/test_terminal_proxy.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

Tests use real loopback HTTP/WebSocket servers and a real agent subprocess for
SIGHUP, replacing only the Docker boundary. They cover generic text ZIPs,
isolation requests, attach-before-start, ownership, immutable profile versions,
byte-preserving terminal input, timeouts, cancellation, cleanup failures, restart
reconciliation, concurrent idempotency, and artifact retention. These tests do
not claim a live Linux gVisor deployment has been exercised.

The 2026-10-15 compatibility gate verified CPython 3.15.0 with `temporalio==1.34.0`,
`psycopg==3.3.6`, `psycopg-binary==3.3.6`, and `psycopg-pool==3.3.3` on macOS arm64,
native Linux arm64, and emulated Linux amd64. Real Temporal worker execution,
cancellation, graceful worker replacement, and replay of four exported histories
passed on each platform. That gate did not exercise PostgreSQL transactions or
process-crash recovery. These dependencies are reserved for the later persistence
and orchestration increment and are not installed by this package yet.

## Independent distribution and topology

[Local infrastructure](infra/README.md) owns the private two-process Compose unit,
Docker build, trusted generic profile and installed operator smoke commands.
`boxie` and `cli-playground` are independent runtime distributions; neither image
installs an infrastructure package. The root lockfile pins both builds. Production
activation remains unavailable until PR4.
