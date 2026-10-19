# Runtime prerequisites

Boxie uses independently operated [PostgreSQL](postgres/README.md)
and production [Temporal](temporal/README.md) on the private control
network. Apply their explicit schema and namespace jobs first. Production never
uses a development/test Temporal server. Temporal certificate holders are trusted
cluster administrators; a namespace is organizational, not an RBAC boundary.

The two Boxie processes are `boxie-api` and `boxie-orchestrator`. The orchestrator
embeds Temporal SDK workers; there is no standalone worker service or worker
configuration. Generate `api.json` and `orchestrator.json` with `boxie_infra.config`
using [config.example.env](config.example.env). Both receive distinct mTLS client
identities. Only the orchestrator receives Docker access and artifact storage.

Migration sources live in `packages/boxie/alembic/`, alongside `alembic.ini`, and are
included in the installed Boxie package. Apply them explicitly:

```sh
uv run --locked --package boxie python -m boxie.persistence.migrate --dsn-file /etc/boxie/postgres-dsn.secret
```

Keep Boxie, Temporal persistence and visibility database credentials separate.
Migration `0002_command_outbox` preserves pending start/stop commands and removes
obsolete observation commands and dispatcher leases. This predecessor is
undeployed; incompatible live workflows require a separately reviewed drain or
versioned cutover. No direct-mode data directory upgrade or PTY restart recovery
is supported.

## Recovery prerequisite for production activation

Backup and restore implementation belongs to the production activation increment.
Before enabling deployment, demonstrate restoration of all three PostgreSQL
databases (Boxie, Temporal persistence and Temporal visibility) and Boxie's artifact
data into a fresh installation, using off-host backups and a documented recovery
point. Verify application accounting, artifact references and Temporal workflow
execution after restoration. Keep the state-backend passphrase and required
certificate material recoverable separately from the running host. Pulumi resource
protection and same-host Docker volumes do not provide this recovery evidence.

## Execution and recovery

Admission atomically reserves a capacity slot, artifact reference and command.
After commit, the API attempts signal-with-start within one second. A crash or
transport failure leaves a pending command; committed acceptance still succeeds.
Status, matching retries and stop requests use SQL during Temporal outages.

The orchestrator holds an exclusive OS lock on its data directory before opening
execution resources. SDK workers poll stable installation/host lifecycle and
maintenance queues. Activities invoke the runtime directly and validate ownership;
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

Startup verifies installation/host-specific Temporal Schedules before serving:

| Operation | Interval | Bounds |
| --- | --- | --- |
| Pending start/stop delivery | 15 seconds | 32 commands, eight concurrent deliveries, 20-second activity deadline |
| Artifact collection | 5 minutes | Bounded existing file/SQL collection batches |
| Journal compaction | 1 minute | Settled runtime, no live sessions, authoritative SQL receipt |
| SQL retention | 5 minutes | Existing retention and ownership constraints |

Schedules skip overlapping runs and catch up at most one minute. Each operation
has its own schedule, with maintenance activity capacity separate from lifecycle
capacity. One failure must not suppress unrelated maintenance or lease renewal.
Missing, paused or incompatible schedules close new admission. Runtime startup
upgrades the original 5-second command and 30-second cleanup intervals to the
cadence above, only when the action, policy and remaining schedule configuration
match the original definitions. Paused or otherwise modified schedules are
rejected. Already upgraded schedules are left unchanged. Namespace/schema
operations remain explicit infrastructure jobs.

These intervals produce about 7,776 scheduled runs per day per host, down from
25,920. Artifact cleanup and SQL retention may wait up to five minutes; sandbox
expiry and lifecycle renewal keep their existing timing.

Successful HTTP health and readiness requests omit request logs and HTTPX response
diagnostics. Request metrics still include these probes. Failed responses,
exceptions, warnings and ordinary request logs remain visible.

New admission also rejects an oldest pending command over 30 seconds or a backlog
of 100. Readiness verifies SQL, orchestrator health, schedule configuration and
recent lifecycle pollers. Poller presence is evidence up to 90 seconds old, not
instantaneous worker liveness.

## Ownership and retention

Only executions whose removal is saved in the journal, without pending create
operations, uncertainty, active activities or terminal sessions, can be compacted.
SQL must confirm the immutable request and terminal accounting release; unavailable SQL never permits
forgetting. Normal admission stops at 128 retained journal records, with eight
additional cleanup-only identities. Journal failures close new admission until a
full save succeeds. An uncertain create plus lost journal writes may
still require operator investigation.

Worker execution shares the orchestrator process and its Docker trust boundary.
Restarting it ends PTYs. Shutdown closes terminal admission, drains SDK work within
its 25-second grace, and retains uncertain physical operations for restart cleanup.

## Verification

Time-skipping workflow tests use the checksum-pinned 1.40.0 executable in
[test-server-pins.json](../tests/test-server-pins.json), supplied as
`BOXIE_TEST_SERVER`. Production tests use the pinned self-hosted Temporal image,
mTLS and real PostgreSQL. Run the isolated native lifecycle harness using:

```sh
uv run --locked python packages/boxie/tests/run_integration.py --runtime runsc
```

The [native lifecycle workflow](../../../.github/workflows/boxie-lifecycle.yml)
provides Linux amd64/gVisor fault evidence. Local Docker/runc or time-skipping tests
do not establish that gate or production activation readiness. Measure schedule
volume and recovery latency without assuming a performance improvement.
