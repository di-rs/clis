# How Boxie runs sandboxes

Boxie separates accepting a request from running its container. The API records
ownership and admission in PostgreSQL; the supervisor owns Docker, terminal
connections, and artifact files. Temporal coordinates work that must survive
retries and restarts.

This guide explains those execution and recovery guarantees. For client requests,
use the [consumer API](consumer-api.md); for process settings, use
[configuration](configuration.md).

- [Isolation and lifetime](#isolation-and-lifetime)
- [Artifacts](#artifacts)
- [Private protocol](#private-protocol)
- [Embedded Temporal execution](#embedded-temporal-execution)
- [Source organization](#source-organization)

## Isolation and lifetime

Containers run under gVisor (`runsc`) as user and group `65534:65534`. The root
filesystem and artifacts are read-only, networking is disabled, all capabilities
are dropped, and `no-new-privileges` is enabled. The admitted command replaces
the image entrypoint.

| Resource | Limit |
| --- | --- |
| RAM, including swap allowance | 512 MiB |
| CPU | One CPU |
| Processes | 256 PIDs |
| Open files | 1024 |
| `/workspace` tmpfs | 64 MiB |
| `/tmp` tmpfs | 16 MiB |
| Total terminal output | 16 MiB |

Both tmpfs mounts use `nosuid,nodev,noexec`.

### Deadlines and shutdown

| Deadline | Default | Starts or resets when |
| --- | --- | --- |
| Absolute lifetime (`max_seconds`) | 3600 seconds | Creation is accepted, including provisioning time |
| Idle (`idle_seconds`) | 600 seconds | The sandbox becomes ready; resets after Docker accepts nonempty user-origin input |
| Disconnected (`disconnect_seconds`) | 120 seconds | The terminal disconnects |

Resize events, output, reads, and reconnect attempts do not reset idle time.
Cleanup operations are bounded and run independently, so an unresponsive container
cannot block other deadlines.

Terminals close when termination begins. Capacity slots and artifacts remain
reserved until Docker confirms deletion. An API restart does not stop the
supervisor; a supervisor restart can end terminals.

### Recover after a crash

Before creating a container, Boxie saves its intent and uses `fsync` on both the
file and directory. Deterministic names and labels identify the installation,
host, sandbox, generation, consumer, artifact, and deadline.

Recovery cleans up only resources with matching ownership labels. It never deletes
every container on the Docker daemon. If another resource occupies the expected
name with different labels, Boxie leaves it for operator inspection.

A timed-out create request is ambiguous: Docker may still create the container
later. Until Boxie observes and removes that owned container, cleanup stays
pending and holds its capacity slot and artifact references. This case can require
operator investigation.

## Artifacts

### Validate an upload

Boxie hashes the streamed ZIP bytes, checks the supplied SHA-256, and extracts
into an unpublished directory. It checks actual expanded sizes rather than
trusting sizes declared in the archive.

| Limit | Maximum |
| --- | --- |
| Compressed upload | 512 MiB |
| Total expanded bytes | 512 MiB |
| One member | 64 MiB |
| Regular files | 1024 |
| Central directory | 4 MiB |
| Central-directory entries, including directories | 2048 |

A bounded preflight scans the central directory before constructing the ZIP
parser. Advertised counts cannot bypass that scan. Classic ZIP and bounded ZIP64
metadata are accepted, with stored or deflate compression.

Uploads reject absolute paths, traversal, backslashes, duplicate normalized
names, links, devices, encrypted entries, and unsupported compression. Nested
text and executable files are both supported. Files receive mode `0555` to
remove unsafe permission bits; directories receive traversable permissions.
No consumer can select a host filesystem path.

### Publish and collect files

Publication atomically renames the extracted directory before committing SQL
metadata. If the commit outcome is uncertain, Boxie retains the bytes for
SQL-based reconciliation.

The periodic collector removes retired, unreferenced files and old unpublished
directories only after checking SQL. If SQL is unavailable, it deletes nothing.

An artifact UUID and digest are immutable. Repeating the same upload is idempotent;
a different digest returns 409. Both private HTTP APIs return 201 for new
publication and 200 for an exact retry.

Retirement is committed to PostgreSQL before acknowledgement and prevents new
admission. Physical files remain while a sandbox reference exists, including uncertain
creation or failed deletion. Metadata tombstones preserve UUID immutability after
collection.

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

### Admission and capacity

The internal accepted `spec` records the resolved profile, command, environment
and policy alongside the artifact ID. The public API does not expose those choices.
A repeated consumer/idempotency key returns the same ID only for the exact
canonical specification; changed requests return 409. Capacity is eight sandboxes
by default, including uncertain creation and pending deletion. The
[consumer API](consumer-api.md#request-sequence) describes the returned status.

### Prevent execution after a stop

Before creating a journal record, the ensure operation rechecks the immutable
admission stored in SQL. A stopped admission can only initialize cleanup. A
cleaned, missing, or mismatched admission cannot recreate execution.

The final Docker start and a committed stop request use the same SQL row lock.
That prevents startup from racing past a stop. Capacity is released only when
container absence has been confirmed for the current execution generation.

### Terminal relay

The private terminal uses the same output sequences and JSON controls as the
[consumer terminal](consumer-api.md#connect-a-terminal). Handshake denials return
bounded HTTP JSON responses:

| Status | Supervisor denial |
| --- | --- |
| 401 | Invalid supervisor credentials |
| 404 | Resource ownership is unavailable |
| 409 | The lifecycle state does not allow attachment |

The API authenticates consumers before contacting the supervisor. It maps an
internal authentication failure or an unsupported denial code to 503.

Only a browser-origin attach after downstream WebSocket acceptance acquires an
epoch. Opening the private relay alone does not renew disconnect grace. The
supervisor attaches to Docker before starting the container, preserving initial
output even without Docker logs.

## Embedded Temporal execution

The supervisor embeds Temporal SDK workers on lifecycle and maintenance queues
scoped to its installation and host. An OS owner lock prevents a second process
from using the same data directory. A
supervisor restart also restarts its workers and ends existing terminals.

### Scheduled work and local safety

Provisioning activities await the local execution. Normal cleanup retries,
30-second inspection, command recovery, artifact collection, journal compaction
and SQL retention are driven by Temporal. Local safety enforcement and journal
flushes remain independent of Temporal availability.

### Publish lifecycle changes

Attachment and termination facts publish directly to SQL; there is no observation
HTTP callback or lifecycle control HTTP protocol. Input remains local, with SQL
facts repaired by the next 30-second workflow inspection. No input or PTY output
is stored in workflow history. The start/stop outbox protects the commit-to-Temporal
handoff; native scheduled recovery handles pending commands without custom leases
or application retry clocks.

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
references. Persisted `ExecutionRecord` values retain the journal format.
A terminal session pins its execution until WebSocket delivery and closing finish;
retention requires settled local work, a saved removal confirmation and the
control-plane receipt before removing the record and execution together.

Network adapters validate incoming terminal controls once and return typed events
to relays, which serialize them for the next connection.
