# How Boxie runs sandboxes

## Private protocol

Every HTTP request and WebSocket handshake requires `Authorization: Bearer SECRET`.
The supervisor uses constant-time credential comparison. A trusted API supplies the
consumer identity; resources belonging to another consumer return 404. UUIDs are
opaque resource IDs. Wire schemas reject extra object fields and invalid bounds.

| Operation | Request |
| --- | --- |
| Upload artifact | `PUT /v1/artifacts/{uuid}?consumer_id=...`, raw ZIP body, `X-Content-SHA256` header |
| Retire artifact | `DELETE /v1/artifacts/{uuid}?consumer_id=...` |
| Resolve profile | `POST /v1/profiles/resolve`, `{profile_id, command, env}` |
| Terminal | `WS /v1/sandboxes/{uuid}/terminal?consumer_id=...` |
| Readiness | `GET /readyz`, supervisor identity and dependency results |

The internal accepted `spec` records the resolved profile, command, environment
and policy alongside the artifact ID. The public API does not expose those choices.
Lifetime defaults are `max_seconds: 3600`, `idle_seconds: 600`, and
`disconnect_seconds: 120`. Admission returns a `SandboxView`
with `id`, `state`, `created_at`, `absolute_deadline`, `reason`, and
`cleanup_pending`. A repeated consumer/idempotency key returns the same ID only
for the exact canonical specification; changed requests return 409. Capacity is
eight sandboxes by default, including uncertain creation and pending deletion.

Ensure revalidates the immutable SQL admission before creating a journal record.
Stopped admissions initialize cleanup only; cleaned, missing and mismatched rows
cannot recreate execution. The final Docker start runs beneath the same SQL row
lock used to commit stop requests. Only fenced confirmed absence releases SQL
accounting.

The private terminal uses the same sequenced output and strict JSON controls
as the [consumer terminal](consumer-api.md#consumer-api). Handshake denials use bounded HTTP JSON responses:
401 for invalid supervisor credentials, 404 for unavailable ownership, and 409 for an
unavailable lifecycle state. Consumer authentication is resolved before any supervisor
request. The API maps failed internal authentication or unsupported denial codes
to 503. Only a browser-origin attach after downstream WebSocket acceptance acquires
an attachment epoch. Opening a private relay alone does not renew disconnect grace.
The supervisor attaches to Docker before starting the container so initial output is
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
until Docker deletion is confirmed. The supervisor survives consumer API restarts;
a supervisor restart may end terminals.

Local intent records are written with file and directory fsync before Docker
creation. Deterministic
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
publication result. Retirement is committed to PostgreSQL before acknowledgement
and prevents new admission.
Physical files remain while a sandbox reference exists, including uncertain
creation or failed deletion. Metadata tombstones preserve UUID immutability after
collection. No consumer can select a host filesystem path.

## Source organization

The consumer API and supervisor transport share explicit wire contracts under
`boxie/contracts/`; cli-playground keeps its own consumer models and has no Boxie
runtime dependency. `application/ports.py` defines the interfaces consumed by the
API. Routes receive those interfaces directly.

The supervisor implementation is grouped by responsibility:

- `sandbox/execution/`: coordination, one execution owner per sandbox, journal,
  provisioning, physical reconciliation and retention.
- `sandbox/terminal/`: PTY reader, bounded replay and WebSocket session delivery.
- `sandbox/transport/`: authenticated supervisor client/server and terminal relay.
- `bootstrap/`: configuration loading, dependency construction, readiness probes
  and background service startup/shutdown.

Each `SandboxExecution` owns its locks, deadlines, tasks, terminal and session
references. Persisted `ExecutionRecord` values retain the existing journal format.
A terminal session pins its execution until WebSocket delivery and closing finish;
retention requires settled local work, a saved removal confirmation and the
existing control-plane receipt before removing the record and execution together.
Network adapters validate incoming terminal controls once and return typed events
to relays, which serialize them for the next connection.

## Embedded Temporal execution

Boxie runs two processes: API and supervisor. The supervisor embeds SDK
workers on installation/host-specific lifecycle and maintenance queues. Its OS
owner lock excludes a second process using the same data directory. A
supervisor restart also restarts its workers and ends existing terminals.

Provisioning activities await the local execution. Normal cleanup retries,
30-second inspection, command recovery, artifact collection, journal compaction
and SQL retention are driven by Temporal. Local safety enforcement and journal
flushes remain independent of Temporal availability. SQL admission/stop and the
final Docker start share the existing SQL row guard.

Attachment and termination facts publish directly to SQL; there is no observation
HTTP callback or lifecycle control HTTP protocol. Input remains local, with SQL
facts repaired by the next 30-second workflow inspection. No input or PTY output
is stored in workflow history. The start/stop outbox protects the commit-to-Temporal
handoff; native scheduled recovery handles pending commands without custom leases
or application retry clocks.
