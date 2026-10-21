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
- [Native maintenance](#native-maintenance)
- [Ownership and retention](#ownership-and-retention)

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

Admission atomically reserves a capacity slot, artifact reference and command.
After commit, the API attempts signal-with-start within one second. A crash or
transport failure leaves a pending command; committed acceptance still succeeds.
Status, matching retries and stop requests use SQL during Temporal outages.

The supervisor holds an exclusive OS lock on its data directory before opening
execution resources. SDK workers poll stable installation/host lifecycle and
maintenance queues. Activities invoke the Supervisor directly and validate ownership;
boot/generation fences and SQL's final-start lock remain mandatory.

The lifecycle workflow awaits provisioning, inspects/renews every 30 seconds,
and retries cleanup across worker restarts with a ten-second maximum retry interval
and no overall cleanup timeout. Removal completes only when journaled physical
absence is accepted by SQL with accounting release. Uncertain creation/deletion retains
both capacity and artifact references. Local monotonic deadlines enforce absolute,
idle, disconnect and control-lease limits during dependency outages. Safety retries
cannot release accounting or assume that an unseen pending create was cancelled.

Attachment and termination events make a bounded direct SQL projection and
workflow notification. Failed notifications are repaired by activity inspection.
Input timestamps are journal-flushed on a one-second cadence and can lag in SQL
by one inspection interval; expiry uses current local facts. PTY bytes never enter
SQL observations or Temporal history. Terminal framing/replay remain unchanged.

## Native maintenance

Pending start/stop delivery runs in a supervised loop in the supervisor process.
It starts immediately, attempts up to 32 commands with eight concurrent deliveries
and a 20-second deadline, then waits five seconds before the next pass. Failed
deliveries stay pending and produce warnings; an empty pass creates no Temporal
workflow or log event.

Startup creates or verifies two installation/host-specific Temporal Schedules
before serving. Both run at midnight and noon UTC, skip overlapping runs and
have a 12-hour catch-up window:

| Operation | Interval | Bounds |
| --- | --- | --- |
| Artifact collection | 12 hours | Drain eligible artifacts in batches of 100, then scan orphan files |
| SQL retention | 12 hours | Compact eligible journal records, then purge eligible SQL rows in batches of 1,000 |

Each activity attempt has a 20-second deadline and a 60-second overall retry
deadline. Maintenance activity capacity is separate from lifecycle capacity;
one failed operation does not suppress the other schedule or lease renewal.

Startup upgrades the shipped 30-second and five-minute cleanup schedules and
deletes the retired command and journal schedules at their known old intervals
(5 or 15 seconds for commands, 30 or 60 seconds for journals). Other schedule
changes, including pauses, are rejected. Handlers remain available for maintenance
workflows that already started. Missing, paused or incompatible cleanup schedules
close new admission.

New admission also rejects an oldest pending command over 30 seconds or a backlog
of 100. Readiness verifies SQL, supervisor health, schedule configuration and
recent lifecycle pollers. Poller presence is evidence up to 90 seconds old, not
instantaneous worker liveness.

## Ownership and retention

Before a lifecycle workflow finishes, its facts activity compacts the local journal
record once SQL confirms cleanup. If the record still has unsettled work, the
activity retries with `CleanupPending`. One startup compaction pass and the
twice-daily retention schedule also reclaim eligible records from older completed
workflows.

Only executions whose removal is saved in the journal, without pending create
operations, uncertainty, active activities or terminal sessions, can be compacted.
SQL must confirm the immutable request and terminal accounting release; unavailable SQL never permits
forgetting. Normal admission stops at 128 retained journal records, with eight
additional cleanup-only identities. Journal failures close new admission until a
full save succeeds. An uncertain create plus lost journal writes may
still require operator investigation.

Worker execution shares the supervisor process and its Docker trust boundary.
Restarting it ends PTYs. Shutdown closes terminal admission, drains SDK work within
its 25-second grace, and retains uncertain physical operations for restart cleanup.

## Verification

Time-skipping workflow tests use the checksum-pinned 1.40.0 executable in
[test-server-pins.json](../tests/test-server-pins.json), supplied as
`BOXIE_TEST_SERVER`. Production tests use the pinned self-hosted Temporal image,
mTLS and real PostgreSQL. Run the isolated native lifecycle harness using:

```sh
uv run python packages/boxie/tests/run_integration.py --runtime runsc
```

The [native lifecycle workflow](../../../.github/workflows/boxie-lifecycle.yml)
provides Linux amd64/gVisor fault evidence. Local Docker/runc or time-skipping tests
do not establish that gate or production activation readiness. Measure schedule
volume and recovery latency without assuming a performance improvement.
