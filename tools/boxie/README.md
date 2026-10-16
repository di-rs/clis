# Boxie sandbox service

Boxie is an independently deployed private sandbox service. PostgreSQL owns
admission, idempotency, eight capacity slots and artifact references. The API
commits requests and an outbox; a separate Temporal worker drives the durable
lifecycle. Only the sandbox orchestrator receives Docker access and owns artifact bytes,
PTYs and the execution journal. Consumers select approved profiles and upload ZIP
files; they cannot supply images, host paths or isolation settings. Boxie does not
inspect executable formats or depend on Playground.

Production requires the [durable runtime](infra/durable-runtime.md); there is no
direct lifecycle bootstrap. Run exactly one orchestrator against its data directory.
The directory and profile files must be writable only by the operator/orchestrator. The
Docker daemon must see artifact paths at the same absolute location as the orchestrator.

## Run

Use the root workspace and lockfile:

```sh
uv sync --locked --all-packages
uv run --locked --package boxie boxie-orchestrator --config /etc/boxie/orchestrator.json
```

An operator configuration file contains:

```json
{
  "data_dir": "/var/lib/boxie",
  "postgres_dsn_file": "/run/secrets/postgres-dsn",
  "api_url": "http://boxie-api:8090",
  "profiles_file": "/etc/boxie/profiles.json",
  "credential_file": "/run/secrets/boxie-orchestrator",
  "identity": {
    "installation_id": "b18283b1-dc32-46e7-8da5-7aa380043685",
    "host_id": "sandbox-1"
  },
  "bind_host": "127.0.0.1",
  "bind_port": 8091
}
```

Generate a unique installation UUID once and preserve it with the data directory.
The credential is a separate orchestrator-control secret, at least 16 printable ASCII
bytes, in a private regular file (mode `0600`). Consumer API credentials must not
be reused here. Only trusted services on a private network may reach the orchestrator;
there is no public endpoint. Access logging is disabled by the entry point and
credentials are never accepted in URLs.

Run the separate consumer API without a Docker socket or artifact host mounts:

```sh
uv run --locked --package boxie boxie-api --config /etc/boxie/api.json
```

Its operator configuration contains:

```json
{
  "orchestrator_url": "http://boxie-orchestrator:8091",
  "orchestrator_credential_file": "/run/secrets/boxie-orchestrator",
  "consumer_credential_files": {
    "playground": "/run/secrets/boxie-playground"
  },
  "release": "boxie-durable",
  "identity": {
    "installation_id": "b18283b1-dc32-46e7-8da5-7aa380043685",
    "host_id": "sandbox-1"
  },
  "durable": {
    "postgres_dsn_file": "/run/secrets/postgres-dsn",
    "temporal_address": "temporal:7233",
    "temporal_server_name": "temporal",
    "temporal_ca_file": "/run/secrets/temporal-ca.pem",
    "temporal_cert_file": "/run/secrets/temporal-client.pem",
    "temporal_key_file": "/run/secrets/temporal-client.key"
  },
  "request_timeout": 20,
  "bind_host": "127.0.0.1",
  "bind_port": 8090
}
```

Each credential is a private regular file with the same restrictions as the orchestrator
secret. Consumer credentials must be unique and distinct from the orchestrator secret.
Only composition roots read secrets; transports receive injected credentials.
The API resolves consumer identity from Bearer authentication and never accepts
consumer identity in a sandbox request body. Neither API nor orchestrator is public.
HTTP and WebSocket redirects are disabled for orchestrator calls. HTTP control bodies
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

Sandbox POST sends the `spec` object directly, without the orchestrator's ownership or
idempotency envelope. Unknown fields, images, paths and Docker settings are
rejected. Invalid bodies return 422, conflicting retries 409, capacity exhaustion
429 and unavailable orchestrator calls 503. API restarts retain SQL-owned IDs,
idempotency records and capacity; the orchestrator keeps draining terminal output.

Terminal attachments start with a JSON text frame
`{"type":"attach","attachment_order":1,"resume_after":0,"cols":80,"rows":24}`. The response
`{"type":"attached","epoch":1}` fences subsequent controls. Input carries
`{"type":"input","epoch":1,"source":"user","data":"echo hello\n"}`;
parser-generated replies use `source: terminal` and do not renew idle time.
The consumer allocates a strictly increasing `attachment_order` per sandbox
execution (1–9007199254740991). The orchestrator persists the highest accepted order with
its attachment state before returning success. Equal/older orders fail before any
epoch or grace change, even when an older request was already in flight through a
relay. A new attempt or retry after an uncertain result must use a higher order;
repeating an accepted order is rejected, not an idempotent attach replay. A failed
cursor validation does not consume the order. Journal failure exposes no successful
attachment and disables execution readiness until the pending state is persisted.
Playground supplies this value from its durable page claim; browser frames cannot
select or override it.
ACK, resize, heartbeat and detach also carry the current epoch. Controls are at
most 16 KiB of UTF-8 including JSON, columns are 1–500 and rows 1–300.

Each binary output frame contains an eight-byte unsigned big-endian sequence
followed by at most 16 KiB of raw PTY bytes. Sequences start at one and continue
across attachments. ACK only after applying bytes; reconnect with the last applied
sequence. The orchestrator retains at most 1 MiB of payload and 4096 frames while draining
independently of readers. An evicted cursor returns `stream_gap`; a future cursor
is invalid. Replayed bytes and control metadata do not count toward the 16 MiB
cumulative PTY output limit. JSON status frames contain `type: status` and a
termination `reason`.

Geometry controls identify the preceding sequence. Drain writes to that boundary
before applying dimensions; repeated dimensions are idempotent. After
`replay_complete`, drain writes and request resize, then wait for `resized` before
enabling user input. Parser replies remain allowed during replay. Geometry is
retained per output frame plus one current tail value, including when a resize
acknowledgment is lost. This defines observable frame-assignment/resize-success
order, not exact kernel-time causality for bytes already queued in Docker.

Send browser-origin heartbeat controls every ten seconds; the orchestrator disconnects
a path after thirty seconds without one. Relay-local WebSocket pings do not renew
that deadline. A detected disconnect retains the terminal for its two-minute
grace, subject to the earlier absolute or idle deadline. A successful replacement
attachment receives a new epoch; stale controls cannot affect it. Credentials and
cursors stay in page memory. Page reload starts fresh; restart continuity and
screen snapshots are not supported.

`GET /healthz` is unauthenticated liveness. Authenticated `GET /readyz` returns
the API release and SQL, orchestrator and Temporal readiness, with 503 when unavailable.
The orchestrator's authenticated `/readyz` reports Docker, SQL and execution persistence. Access logs are disabled by both entry points.

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
Approved versions are retained by configuration hash, including across orchestrator
restarts, so delayed requests can validate their original snapshot. Reloads never
change an admitted sandbox's image, command, environment, or limits.

## Private protocol

Every HTTP request and WebSocket handshake requires `Authorization: Bearer SECRET`.
The orchestrator uses constant-time credential comparison. A trusted API supplies the
consumer identity; resources belonging to another consumer return 404. UUIDs are
opaque resource IDs. Wire schemas reject extra object fields and invalid bounds.

| Operation | Request |
| --- | --- |
| Upload artifact | `PUT /v1/artifacts/{uuid}?consumer_id=...`, raw ZIP body, `X-Content-SHA256` header |
| Retire artifact | `DELETE /v1/artifacts/{uuid}?consumer_id=...` |
| Resolve profile | `POST /v1/profiles/resolve`, `{profile_id, command, env}` |
| Execute admitted request | `POST /v1/sandboxes/{uuid}/ensure`, complete immutable SQL request and `X-Orchestrator-Boot` |
| Inspect/renew/remove/expire | `POST /v1/sandboxes/{uuid}/{operation}`, consumer, installation, host, generation and boot fences |
| Terminal | `WS /v1/sandboxes/{uuid}/terminal?consumer_id=...` |
| Readiness | `GET /readyz`, orchestrator identity and dependency results |

`spec` contains `artifact_id`, `profile_id`, `command`, `env`, and optional `policy`.
Policy defaults are `max_seconds: 3600`, `idle_seconds: 600`, and
`disconnect_seconds: 120`; each may be lowered. Admission returns a `SandboxView`
with `id`, `state`, `created_at`, `absolute_deadline`, `reason`, and
`cleanup_pending`. A repeated consumer/idempotency key returns the same ID only
for the exact canonical specification; changed requests return 409. Capacity is
eight sandboxes by default, including uncertain creation and pending deletion.

Ensure revalidates the immutable SQL admission before creating a journal record.
Stopped admissions initialize cleanup only; cleaned, missing and mismatched rows
cannot recreate execution. The final Docker start runs beneath the same SQL row
lock used by durable stop. Only fenced confirmed absence releases SQL accounting.

The private terminal uses the same sequenced output and strict JSON controls
as the consumer terminal above. Handshake denials use bounded HTTP JSON responses:
401 for invalid orchestrator credentials, 404 for unavailable ownership, and 409 for an
unavailable lifecycle state. Consumer authentication is resolved before any orchestrator
request. The API maps failed internal authentication or unsupported denial codes
to 503. Only a browser-origin attach after downstream WebSocket acceptance acquires
an attachment epoch. Opening a private relay alone does not renew disconnect grace.
The orchestrator attaches to Docker before starting the container so initial output is
retained even without Docker logs.

## Isolation and lifetime

Containers run as `65534:65534` under gVisor (`runsc`), with a read-only root,
no network, all capabilities dropped, and `no-new-privileges`. Limits are 512 MiB
RAM (including swap allowance), one CPU, 256 PIDs, nofile 1024, a 64 MiB `/workspace`
and 16 MiB `/tmp` tmpfs, and 16 MiB total terminal output. The tmpfs mounts use
`nosuid,nodev,noexec`. Artifacts are mounted read-only. The admitted command
replaces the image entrypoint.

Absolute lifetime begins at accepted creation, including provisioning. Idle time
begins when ready and resets only after nonempty user-origin input is accepted by Docker.
Resize, output, reads, and reconnect attempts do not reset it. Disconnection
starts a separate two-minute deadline. Cleanup operations are bounded and run
independently so an unresponsive container does not block other deadlines.
Terminals close when termination begins; slots and artifacts remain reserved
until Docker deletion is confirmed. The orchestrator survives consumer API restarts;
an orchestrator restart may end terminals.

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
before SQL metadata commit. An uncertain COMMIT retains bytes for SQL-based
reconciliation. The periodic collector deletes retired unreferenced bytes and
old unpublished directories only after checking SQL; unavailable SQL deletes nothing.

An artifact UUID and digest are immutable. Repeating the same upload is idempotent;
a different digest returns 409. Both private HTTP APIs return 201 for new
publication and 200 for an exact retry, using an explicit typed application
publication result. Retirement is durable and prevents new admission.
Physical files remain while a sandbox reference exists, including uncertain
creation or failed deletion. Metadata tombstones preserve UUID immutability after
collection. No consumer can select a host filesystem path.

## Verification and feasibility

```sh
uv run --locked pytest tools/boxie/tests/test_orchestrator.py tools/boxie/tests/test_artifacts.py tools/boxie/tests/test_profiles.py
uv run --locked pytest tools/boxie/tests/test_service_boundary.py tools/boxie/tests/test_terminal_proxy.py
uv run --locked pytest -m integration tools/boxie/tests/test_api.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

Fast terminal tests use real loopback HTTP/WebSocket servers, durable orchestrator
execution, and explicit test-only SQL admission/artifact metadata and Docker
boundaries. Integration tests use real PostgreSQL and production Temporal;
profile reload also starts the real orchestrator subprocess. They cover generic text ZIPs,
isolation requests, attach-before-start, ownership, immutable profile versions,
byte-preserving terminal input, timeouts, cancellation, cleanup failures, restart
reconciliation, concurrent idempotency, and artifact retention. These tests do
not claim a live Linux gVisor deployment has been exercised.

The 2026-10-15 compatibility gate verified CPython 3.15.0 with `temporalio==1.34.0`,
`psycopg==3.3.6` and `psycopg-binary==3.3.6` on macOS arm64,
native Linux arm64, and emulated Linux amd64. Real Temporal worker execution,
cancellation, graceful worker replacement, and replay of four exported histories
passed on each platform. That gate did not exercise PostgreSQL transactions or
process-crash recovery. These pinned dependencies are installed by the durable runtime. The mandatory
[native lifecycle workflow](../../.github/workflows/boxie-lifecycle.yml) additionally
exercises Linux amd64/runsc, process loss, SQL accounting and browser reconnection.

## Independent distribution and topology

[Local infrastructure](infra/README.md) owns the private API/worker/orchestrator Compose unit,
Docker build, trusted generic profile and installed operator smoke commands.
`boxie` and `cli-playground` are independent runtime distributions; neither image
installs an infrastructure package. The root lockfile pins both builds. Production
activation remains unavailable until PR4.

## Source organization

The consumer API and agent transport share explicit wire contracts under
`boxie/contracts/`; Playground keeps its own consumer models and has no Boxie
runtime dependency. `application/ports.py` defines the interfaces consumed by the
API. Routes receive those interfaces directly.

The agent implementation is grouped by responsibility:

- `sandbox/execution/`: coordination, one runtime owner per sandbox, journal,
  provisioning, physical reconciliation and retention.
- `sandbox/terminal/`: PTY reader, bounded replay and WebSocket session delivery.
- `sandbox/transport/`: authenticated agent client/server and observation reporting.
- `bootstrap/`: configuration loading, dependency construction, readiness probes
  and background service startup/shutdown.

Each `ExecutionRuntime` owns its locks, deadlines, tasks, terminal and session
references. Persisted `ExecutionRecord` values retain the existing journal format.
A terminal session pins its runtime until WebSocket delivery and closing finish;
retention requires settled local work, a saved removal confirmation and the
existing control-plane receipt before removing the record and runtime together.
Network adapters validate incoming terminal controls once and return typed events
to relays, which serialize them for the next connection.
