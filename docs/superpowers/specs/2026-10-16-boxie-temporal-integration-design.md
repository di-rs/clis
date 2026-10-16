# Boxie embedded Temporal design

Status: approved for implementation in PR #40. The user authorized execution after reviewing this design and its plan.

Target: PR #40 (`feat/boxie-temporal`), reviewed at
`d6a87df2d50a486998955dc59afb46f8cf93d8f1`. Recheck the head before implementation.

## Intent

Temporal was introduced to replace self-driven worker machinery. Integrate it
into Boxie's orchestrator to resume execution, retries, waits, and scheduled
maintenance after worker restarts. Keep Boxie's domain and public API in Boxie.
Use **orchestrator** consistently; there is no additional component named agent.

Success means deleting redundant scheduling and transport code, not merely
moving existing loops into a new directory or adding another controller.

## Approach and alternatives

Chosen: embed Temporal SDK workflow and activity workers in the orchestrator.
Activities invoke its execution runtime directly. PostgreSQL retains admission,
stop intent, capacity, and artifact accounting. Temporal records lifecycle
progression and retries normal cleanup across worker restarts. The runtime retains
physical ownership.

Keeping a separate worker and only refactoring adapters would preserve most of
the protocol and background machinery. Making Temporal the admission database
would require changing availability and cross-sandbox transaction semantics.
Neither is the objective of this PR revision.

Temporal deployment is owned under `tools/boxie/infra/temporal/` and remains
independently operated, with explicit server and schema releases. Its SDK worker is a
necessary executor embedded in the application, not a third Boxie service.
Workflows remain deterministic; Docker, SQL, filesystem, clocks, and runtime
objects are accessed only inside activities or the ordinary live I/O path.

## Process and ownership boundaries

| Owner | Responsibilities |
| --- | --- |
| API process | Authentication, SQL admission/status/stop, immediate command delivery, artifact/terminal relay |
| Orchestrator process | Embedded SDK workers, execution runtime, Docker, PTYs, journal, local safety |
| PostgreSQL | Atomic reservations, immutable request, stop guard, observations, confirmed accounting release |
| Temporal service | Activity dispatch/retries, persisted workflow timers/history, recovery and maintenance schedules |

One orchestrator owns one installation/host data directory. Reject a second owner
with an OS-held exclusive lock before Docker access or worker polling. Derive
task queues from installation ID and host ID, never boot ID. Check the same
identity in activities. Do not introduce generic fleet scheduling or failover.

Use a host lifecycle queue and a separate host maintenance queue, with embedded
SDK workers in the same process. Maintenance outages must not exhaust lifecycle
activity slots. Preserve boot/generation fences; Temporal task routing does not
replace physical ownership checks.

## Sandbox flow

1. API commits admission, one of eight slots, the artifact reference, and a start
   command in one SQL transaction. Existing idempotency semantics remain.
2. After commit, API attempts idempotent signal-with-start, with a one-second
   total delivery budget. Failure leaves the command pending and still returns
   the committed acceptance. Retried API requests also attempt pending delivery.
3. A lifecycle workflow invokes local provisioning through a regular Temporal
   activity. It awaits completion instead of starting an untracked provision
   worker and polling an HTTP endpoint. Only opaque IDs enter workflow inputs;
   activities load sensitive immutable request data from SQL.
4. The workflow waits for stop/lifecycle notifications, its absolute deadline,
   or a 30-second inspection timer. A local activity checks current SQL intent,
   reads runtime facts, projects them to SQL, and renews the 90-second control
   lease only when execution is still permitted. Idle/disconnect expiry uses
   the runtime's authoritative monotonic deadlines, not a second calculation
   from asynchronously reported timestamps.
5. Stop commits in SQL before ordinary cleanup. Stop API acknowledgement means
   stop intent committed to PostgreSQL, not completed physical deletion. The same
   bounded delivery path wakes the workflow; SQL-only status/stop remain available when
   Temporal is unavailable.
6. Native Temporal retries drive bounded removal attempts without an overall
   cleanup deadline. Completion requires journaled, fenced physical absence and
   committed SQL release of capacity/artifact references. Cancellation requests
   cleanup; it never declares success or abandons the obligation.

Provisioning retries must reuse a live runtime and must not attach a second PTY,
repeat Docker start, or overlap a pending create after a lost acknowledgement.
Activity timeout does not prove that Docker cancelled an operation. Keep the
pending create handle and conservative journal uncertainty until resolved.
Do not swallow provisioning errors and report activity success.

## Eliminate the observation round trip

Remove orchestrator-to-API observation HTTP reporting and observation outbox
commands. The orchestrator already has SQL access. Activities persist lifecycle
facts directly through the fenced repository method.

Live attachment/detachment/exit/safety-stop events make one bounded attempt to
persist their latest journaled fact and notify the workflow with an opaque
sandbox/version hint. Do not create a task per input frame or a reporting retry
loop. Failed delivery is repaired by the next workflow inspection or restart
recovery. Input timestamps remain local and journal-flushed on the existing
one-second cadence; SQL observation freshness may be up to 30 seconds for input
alone. Expiry never relies on that stale SQL timestamp.

Notifications are hints and can be lost or duplicated. SQL stop intent and
physical facts remain authoritative. Confirmed cleanup must be committed before
the workflow closes. Public terminal framing, replay, and attachment fencing
remain unchanged.

## Replace background scheduling

Keep a minimal transactional command outbox for start/stop only. This is the
SQL-to-Temporal atomicity bridge, not a second workflow engine. Remove command
lease owners, lease expiry, attempt counts, next-attempt timestamps, and custom
backoff. Delivery can overlap: immutable event IDs, deterministic workflow IDs,
idempotent notifications, and conditional acknowledgement make duplicates safe.

Use Temporal Schedules for bounded recovery/maintenance workflows:

- Pending-command recovery: every 5 seconds, at most 32 commands per batch and
  eight deliveries concurrently. A command failure must not prevent attempts
  for the rest of the batch. The activity has a 20-second schedule-to-close
  budget; later scheduled runs retry remaining commands. Retain the 30-second
  oldest-command and 100-command admission readiness bounds.
- Artifact collection, journal compaction, and SQL retention: independent
  schedules every 30 seconds, separate bounded activities, so failure of one
  does not suppress the others. Preserve existing collection timeouts/guards.

Schedules use SKIP overlap and a bounded catch-up window of one minute, since
each run examines current state. No replay of every missed tick is needed.
Use stable installation/host schedule IDs and verify their configuration during
startup. Missing or incompatible schedules keep new admission unready.
Bootstrap starts workers and registers/verifies schedules without waiting for
admission readiness, avoiding a circular startup dependency. Existing schedule
definitions are not silently overwritten if incompatible.

This replaces dispatcher polling, worker retention polling, orchestrator
collection polling, and normal reconciliation spawning. The tradeoff is native
Temporal schedule execution/history overhead; measure it in the integration
gate instead of claiming this makes the system faster.

## Local work that must remain

Retain PTY readers, WebSocket I/O, bounded replay buffers, session pinning,
per-execution locks, journal writes, and pending Docker operation tracking.
PTYs, WebSocket connections, and replay buffers reside in memory and are lost
on orchestrator restart; Temporal resumes workflow scheduling from its history.

Extract a narrowly scoped local safety watchdog. It enforces the earliest of
the 60-minute absolute limit, 10-minute input inactivity, two-minute disconnect
grace, and 90-second control lease, honoring lower admitted policy values. It
closes terminal access and performs ownership-checked emergency removal without
requiring SQL/Temporal availability. If physical removal fails, it must keep
attempting to enforce safety while the process is alive; this is an explicit
exception to removing local retries. It cannot release accounting or invent
confirmed absence. Journal flush recovery also remains local.

Normal cleanup and retention must not run in that watchdog. On startup, load
journal records and fail closed for prior-boot executions before accepting
work; Temporal activities subsequently reconcile and settle their accounting.
No PTY is restored after orchestrator/host restart. On shutdown, close terminal
admission first, drain/cancel SDK work within the existing 25-second grace, and
preserve uncertainty for any unsettled physical operation before closing clients.

## What is removed and what remains

| Existing machinery | Target |
| --- | --- |
| `boxie-worker`, `bootstrap/worker.py`, worker service/config | Embedded SDK workers in orchestrator bootstrap |
| Lifecycle HTTP ensure/inspect/renew/expire/remove routes and client methods | In-process activity calls |
| `ObservationReporter`, `/internal/observations`, observation outbox | Direct fenced SQL projection and bounded hints |
| Detached `runtime.worker` provisioning task and activity HTTP poll loop | Awaited, idempotent provisioning activity |
| `_sweep` normal reconciliation/compaction task spawning | Workflow retries, maintenance schedules, narrow safety watchdog |
| Dispatcher claim/lease/retry engine | Immediate idempotent delivery and scheduled recovery |
| Artifact collection and SQL purge process loops | Independent scheduled maintenance activities |
| Artifact/profile/readiness HTTP and terminal WebSocket | Retained for API-to-orchestrator communication |
| Journal, SQL start guard, boot/generation fences | Retained physical safety/accounting guarantees |

## Compatibility, tradeoffs, and scope

Keep Python 3.15, the root uv lockfile, SQLModel/Alembic, and temporalio 1.34.0.
No new execution framework, generic workflow abstraction, or alternative runtime.
Keep private mTLS and separate database credentials. The orchestrator now needs
its own Temporal client certificate; the removed worker certificate/config is
retired. The API still has no Docker socket or artifact directory.

Embedding workers couples their process restart to orchestrator restart and
therefore terminal loss. It also puts SDK execution in the Docker-owning trust
boundary. This is accepted for the current single-host topology; document it.
SDK task starvation and blocking journal/filesystem work must be tested with
active terminals. Do not claim independently restartable workers afterward.

PR40 remains undeployed. Verify that before changing workflow definitions/schema
without compatibility branches. Preserve committed migrations; add a revision
for the simplified outbox. Recreate disposable test histories as appropriate,
then export and replay new histories. If real executions exist, stop and design
a drain/versioned cutover rather than replay incompatible code.

Update the relevant PR40 Compose/config/test surfaces. Downstream PR41/PR42
must later be reconciled with this topology; do not silently edit those branches.
Production activation and Temporal infrastructure provisioning remain separate.

## Evidence required

Use real API/SQL/Temporal/orchestrator end-to-end tests, not only mocked activity
tests. Cover duplicate dispatch, process loss around commits, activity timeout
with a pending Docker create, stop/start races, physical deletion with a lost
SQL acknowledgement, Temporal/SQL outages, safety deadlines, host queue isolation,
maintenance failure isolation, replay, and terminal behavior. Finish with the
existing production Temporal and native Linux amd64/gVisor fault gates.

Review must demonstrate that every removed loop has one named replacement and
that no ordinary retry/reconciliation loop survives under a new name. Local
safety retries must be separately identified and justified.

References: [Temporal workflow model](https://docs.temporal.io/workflow-definition),
[Python Schedules](https://docs.temporal.io/develop/python/workflows/schedules),
[Python Continue-As-New](https://docs.temporal.io/develop/python/workflows/continue-as-new).
