# Durable runtime

Boxie requires the independently operated [PostgreSQL](../../../infra/platform/README.md)
and production [Temporal](../../../infra/temporal/README.md) packages on the private
control network. Run their explicit schema/namespace setup first. Production never
starts a Temporal development or test server. All certificate holders are trusted
cluster administrators; the namespace is organizational, not an RBAC boundary.

Prepare a mode-0600 Boxie database DSN file (a PostgreSQL URI or libpq DSN). The
Boxie, Temporal persistence and visibility credentials must remain distinct.
Apply Boxie migrations explicitly from the installed Boxie environment:

```sh
uv run --locked python -m boxie.persistence.migrate --dsn-file /etc/boxie/postgres-dsn.secret
```

Generate `api.json`, `worker.json` and `agent.json` with `boxie_infra.config` as in
[the infrastructure guide](README.md). Provide the additional paths in
[config.example.env](config.example.env). API and worker mount separate Temporal
client identities signed by the dedicated client CA, verify the server's CA and
hostname, and receive no Docker socket or artifact directory. The agent receives
the Boxie DSN and agent credential, but no Temporal certificates.

The API commits admission, capacity and artifact references with a start outbox
record. Only the agent stores artifact bytes; PostgreSQL is the artifact metadata
authority. The worker dispatches durable notifications and executes lifecycle
activities. The agent revalidates the same SQL admission under the final-start row
guard. Stop is durable before cleanup starts. There is no direct-mode startup or
fallback. Do not reuse an old direct-mode data directory; migration and restart
terminal continuity are excluded.

`/readyz` reports orchestration readiness: SQL/outbox age and backlog, authenticated
agent execution/Docker readiness, Temporal health/namespace and recent workflow
and activity pollers. A command older than 30 seconds or backlog of 100 rejects new
admission. Matching retries, status and stop still use SQL when dependencies are
unhealthy. Stop requires the normal API DELETE, never Temporal termination.
Workers can restart without closing agent-owned terminals. Cleanup failures retain
capacity and artifact references and retry durably every 30 seconds after each
bounded cycle. Only fenced physical absence can release accounting.

The agent publishes authenticated `PUT /internal/observations` with its agent
credential. Consumer credentials cannot publish facts. Input reports coalesce for
10 seconds; attachment, detachment, exit and deletion trigger immediate delivery.
One latest fact per sandbox survives an API outage in agent memory; 30-second
workflow inspection repairs lost reports and renews the control lease. Sensitive
profile/command/environment values are resolved only inside activities; no PTY
bytes enter SQL observations or Temporal history.

## Integration test server

Time-skipping tests use the separate pinned test server 1.40.0. Set
`BOXIE_TEST_SERVER` to the platform-specific executable described in
[test-server-pins.json](../tests/test-server-pins.json); tests verify its SHA-256
and fail when it is missing or different. Download the versioned archive, verify
its archive hash, extract the listed member and verify its executable hash.
This test-only binary is never a production input. Run from the repository root:

```sh
UV_CACHE_DIR=/tmp/boxie-uv-cache uv run --locked pytest -m integration \
  tools/boxie/tests/test_temporal_lifecycle.py tools/boxie/tests/test_outbox.py
```

Time-skipping tests use deterministic activities and export/replay real histories.
The separate production-server tests exercise PostgreSQL, mTLS, HTTP, agent fencing
and resource accounting without skipping PostgreSQL or agent clocks. Native
Linux/runsc and full process-crash proof are additional integration gates.

## Execution journal bounds

Only durably removed executions with no pending create, uncertainty or protocol
session can be reclaimed. The agent reads back the exact immutable SQL request
and terminal cleaned state with no slot or artifact reference. This proof survives
prior-boot reports and lost acknowledgments. An authoritative missing row after
SQL retention is also safe: all fresh execution initialization revalidates SQL and
rejects missing/cleaned admissions. SQL errors never authorize forgetting.

Ordinary admission stops at 128 retained records. Cleanup-only initialization has
eight reserved records of headroom, with a hard total ceiling of 136. Existing
cleanup and receipt retries continue while readiness is closed; uncertain
ownership never ages out. Compaction atomically replaces the journal and reclaims
per-execution callbacks/maps only after they settle. Failed writes keep the
in-memory barrier and close readiness until a full durable save succeeds.

User-input idle timestamps and versions change immediately in memory and flush
on a one-second scheduled cadence; critical lifecycle/attachment saves include
all pending input. This is a cadence, not a maximum fsync latency guarantee. There
is at most one pending flush fact, never a task per input. A restart never adopts
the prior PTY, so losing the last input interval cannot extend a resumed session.


Artifact upload and each bounded collector batch use one 20-second total async
deadline, including waiting for their shared file lock. The public client retains
its existing 20-second total deadline. Timeout reports dependency unavailable;
ambiguous publication/COMMIT retains bytes for retry/readback. Synchronous file
operations and driver cancellation unwinding are not a hard wall-clock guarantee.

If a lost create acknowledgment is discovered and physically deleted while all
journal saves fail, then the agent crashes before recording discovery, the next
boot must retain unknown ownership even when Docker reports absence. That case
keeps SQL capacity/artifact references and needs operator investigation; recovery
does not invent finality. A durably known container ID can instead reconcile
absence after storage recovery and confirm cleanup. Neither case resumes a PTY.
