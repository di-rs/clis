# Shish, Boxie, and durable execution

Start by running shish as an operator-approved program inside Boxie's existing
sandbox lifecycle. Keep Temporal connectivity in Boxie. A later durable-job
facility should accept explicitly submitted work with declared recovery policy;
ordinary terminal commands do not become durable jobs automatically.

This is a design recommendation, not a runtime implementation or a new remote
execution API. Shish currently runs locally; Boxie's shipped sandbox uses Bash.
The [platform policy](../../../docs/platform-direction.md#compatibility-and-product-priorities)
keeps shell compatibility, isolation, recovery, and interactive conveniences
separate. Full POSIX conformance is not a prerequisite for this integration.

## Existing boundary and first integration

Boxie's [consumer API](../../boxie/docs/consumer-api.md) admits an artifact under
authenticated ownership and capacity limits. The operator selects the image,
command, environment, and limits through a versioned
[profile](../../boxie/src/boxie/sandbox/profiles.py); consumers cannot substitute
an arbitrary image, command, or host path. The supervisor owns Docker access,
terminal transport, and Temporal workers. The
[execution contract](../../boxie/docs/execution.md) defines provisioning,
reconciliation, deadlines, and cleanup.

The first implementation would package shish and its required external utilities
in a digest-pinned image, admit an absolute shish command through an approved
profile, and exercise the existing PTY transport. Preserve the non-root user,
read-only root/artifacts, disabled network, gVisor runtime, and resource limits.
Current `/workspace` and `/tmp` are ephemeral tmpfs mounts with `noexec`; putting
an executable there will not make it runnable. Put approved binaries in the
image or admitted artifact location instead.

```mermaid
flowchart LR
    User[Consumer and terminal client] --> API[Boxie API: admission and ownership]
    API --> DB[(SQL: admission and outbox)]
    API <--> Supervisor[Boxie supervisor and Python workers]
    DB <--> Supervisor
    Supervisor <--> Temporal[Temporal: lifecycle history]
    Supervisor --> Docker[Docker and gVisor]
    User <-->|terminal through authenticated relay| Supervisor
    Supervisor <-->|PTY bytes| Shell[Shish inside sandbox]
    Docker --> Shell
    Shell --> Programs[Approved image and artifact programs]
    Supervisor -.-> Jobs[Proposed durable-job records and output store]
```

Solid connections represent existing Boxie mechanisms, with shish as the proposed
sandbox program. The dotted job store is future work. Temporal receives lifecycle
identifiers and nonsecret facts; terminal bytes take the supervisor relay path.

## What recovery means

Reattachment reconnects a client to a surviving process. Restart reruns work from
a defined initial state. Resume continues from an application-defined checkpoint.
These have different requirements and must have different acceptance tests.

| Failure | Current Boxie guarantee | Proposed shish integration | Durable-job requirement before claiming more |
| --- | --- | --- | --- |
| Browser or network disconnect | Reattach while execution survives and deadlines allow it; replay is bounded and can report a stream gap. | Same process and in-memory shell state may remain available; no replay of terminal input. | Separate job lifetime from attachment policy only through an explicit admitted policy. Persist output if replay beyond the current buffer is required. |
| API restart | Supervisor may continue execution; committed SQL admission/outbox survive if their storage survives. | Reconnect through the restored API within existing limits. | Keep job identity, owner, and result lookup independent of the HTTP connection. |
| Supervisor restart | PTYs end; ownership journals and SQL fences support cleanup/reconciliation, not session restoration. | Do not promise restoration of shell variables, foreground commands, or terminal state. | Reconcile the previous attempt before any replacement; classify unknown results and preserve resource reservations. |
| Sandbox process/container loss | Lifecycle cleanup can converge; a lost process is not restored. | Files in tmpfs, shell memory, job table, and command progress are lost. | Restart only from durable declared inputs, or use a separately supported checkpoint protocol. |
| Host loss | Local artifacts, journals, and running processes may be unavailable; no cross-host process recovery is promised. | No automatic migration or resumption. | Off-host durable storage, owner fencing, orphan reconciliation, image availability, and a tested host replacement policy. |
| Temporal outage | Boxie retains committed commands and enforces local deadlines; new admission is subject to readiness rules. | Shell execution does not gain a bypass around those limits. | Reconcile submitted intent and completion after recovery; never infer execution absence from a timeout. |
| Completion acknowledgement lost | Lifecycle operations reconcile physical facts. | A command may have changed files or external systems before its result was lost. | Record an uncertain outcome; query an authoritative receipt or require an explicit retry decision. |

The terminal protocol provides
[sequenced output replay](../../boxie/docs/consumer-api.md#connect-a-terminal), not
screen snapshots, persisted stdin, or process memory. Replay after a supervisor
restart is not promised. A lifecycle workflow finishing cleanup says nothing
about whether a shell command completed successfully.

## Explicit durable jobs

Prefer runtime-mediated submission: a trusted Boxie worker owns the job lifecycle
and runs the admitted workload inside a sandbox. The shell is a workload frontend,
not a Temporal worker. Submission still needs a reviewed product/API contract;
this document does not invent or expose an endpoint.

| Design | Benefit | Cost and decision |
| --- | --- | --- |
| Boxie-mediated jobs | Reuses admission, ownership, profile snapshots, SQL fences, supervisor isolation, and existing Python SDK workers. | Add explicit job/attempt state and durable input/output contracts. Recommended starting point. |
| Workers embedded in shish or each sandbox | Can expose shell-specific operations directly as activities. | Requires sandbox access to control credentials/network, worker routing and versioning, and recovery after worker/sandbox loss. Conflicts with the current trust boundary; reject as the initial design. |
| Detached process manager or terminal multiplexer | Useful for client disconnects while a host and process survive. | Does not provide host-loss recovery, side-effect reconciliation, or checkpointing. May complement terminal UX, not replace durable jobs. |
| Application checkpoints | Can resume supported long-running programs from durable progress. | Each program needs checkpoint validation and restore semantics. Do not infer support for arbitrary commands or pipelines. |
| Process/container checkpointing | Could preserve selected process state. | Requires a separate feasibility study for gVisor, kernel/runtime compatibility, open files, sockets, and restored credentials. No current support claim. |

[Temporal replay](https://docs.temporal.io/workflow-execution#replays) reconstructs
workflow decisions from recorded events. It does not capture a subprocess's
memory, file descriptors, or shell AST evaluation position. Run external effects
in activities controlled by the supervisor, with workflows coordinating identifiers
and decisions. [Activity execution](https://docs.temporal.io/activity-execution)
can retry work; the activity must reconcile an attempt instead of blindly launching
a second process.

### Identity, ownership, and immutable inputs

A durable submission needs an owner-scoped job ID and idempotency key, with a
canonical immutable specification. Changed specifications under the same key
must conflict. Each execution attempt needs its own ID, sandbox generation,
owner/host fence, start intent, observed start, and completion receipt. A workflow
run ID alone is not a command identity or evidence that a process started once.

The specification must pin image digest, shish/Brush version, mode, artifact
hashes, working directory, arguments, approved environment version, locale,
resource limits, and retry policy. Secret references need explicit version and
rotation rules without persisting the secret in workflow data. Recovery cannot
silently use a different image, mutable `PATH`, or current shell environment.
Do not snapshot a live interactive shell implicitly; require declared inputs.

### State that must persist

| State | Current behavior | Proposed durable-job contract |
| --- | --- | --- |
| Files | Image/artifacts are read-only; writable tmpfs disappears with execution. | Versioned input manifests and durable output publication with checksums, ownership, retention, and atomic commit rules. Partial output remains marked incomplete. |
| Variables and cwd | Brush holds shell state in process memory. | Explicit initial environment and cwd. No implicit restoration of shell variables, functions, aliases, options, or open descriptors. |
| Output | Bounded terminal replay in the live supervisor. | Separate bounded durable stdout/stderr objects with ordered offsets and terminal result metadata. Account for partial writes and duplicate delivery. |
| Progress | Process memory and external side effects. | An application checkpoint/receipt reference with schema/version and integrity checks, when that job type supports one. Heartbeats alone are liveness observations. |
| Completion | A caller can lose acknowledgement. | Persist attempt identity, exit status or signal, result manifest, and publication receipt before acknowledging success. |

Neither command history nor terminal transcripts are a safe job specification.
Replaying `printf x >> file` may append twice. Exporting variables also misses
shell functions, current directories, file offsets, background processes, and
remote effects.

### Retries, cancellation, and deadlines

Default arbitrary commands to **no automatic execution retry after an uncertain
start or completion**. Classify attempts as not started, running, succeeded,
failed, cancelled with confirmation, or outcome unknown. A timeout is not proof
that the process stopped. Keep accounting and ownership until physical absence
or an authoritative completion receipt is established.

A retry is eligible only when absence of execution is proven, the operation has
an enforceable idempotency contract, or the submitter explicitly accepts duplicate
side effects. A key in SQL does not make an external command idempotent. External
systems need their own deduplication receipts; compensation is a separate action,
not evidence that an effect never happened.

Cancellation records desired stop durably, fences new starts, signals the owned
process group, escalates within a bound, and confirms cleanup. Preserve an
absolute deadline across attempts; a retry must not reset it. Keep resource,
output, idle, attachment, and job deadlines distinct. Workflow cancellation is
not a substitute for signalling and observing processes. Temporal's
[Python error handling](https://docs.temporal.io/develop/python/best-practices/error-handling)
provides retry/failure controls; product-specific retry eligibility remains ours.

### Credentials and sensitive data

Keep Temporal certificates, Docker access, SQL credentials, and consumer/control
credentials outside the sandbox. Boxie's current
[Temporal deployment](../../boxie/docs/temporal.md#trust-and-private-transport)
treats admitted mTLS clients as fully trusted administrators; a namespace is not
an authorization boundary.

Persist only approved opaque IDs and bounded nonsecret facts in workflow inputs,
results, signals, heartbeats, errors, memo, and search attributes. Resolve sensitive
specifications inside trusted activities from an access-controlled store. Keep
terminal input/output, command text containing secrets, environment values, and
checkpoint contents out of history, logs, and metrics. Redact failures before
crossing the activity boundary. Output/checkpoint storage needs encryption,
owner authorization, retention, deletion, and size limits independently of
Temporal history retention.

## Focused implementation proposals

Each row is a separate future PR, with its own acceptance evidence. D01–D02 enable
ordinary shish sandboxes; D03 onward require an explicit durable-job product
contract. None is included in this documentation stage.

| ID | Scope and dependency | Acceptance evidence |
| --- | --- | --- |
| D01 | Digest-pinned shish image and approved profile; depends on shell foundation. | Linux/gVisor launch, non-root/read-only/no-network enforcement, executable locations, and profile rejection tests. |
| D02 | Shish terminal transport gate; depends on D01. | PTY sizing, signals, foreground groups, disconnect/reconnect, stream gaps, deadlines, and supervisor restart showing no false resume claim. |
| D03 | Owner-scoped durable job/attempt schema and submission contract. | Changed-key conflicts, immutable inputs, generation fences, outcome-unknown states, and migration tests; no command execution yet. |
| D04 | Durable input/output storage and publication; depends on D03. | Owner isolation, checksums, quotas, partial publication, crash consistency, and retention. |
| D05 | Supervisor-mediated job lifecycle; depends on D03–D04. | Lost start/completion acknowledgements reconcile without duplicate launch; workflow replay and worker-version tests. |
| D06 | Cancellation and absolute deadline enforcement; depends on D05. | Stop/start races, descendants, dependency outages, and cleanup accounting. |
| D07 | Opt-in retry policies and external receipts; depends on D05–D06. | Non-idempotent append example remains unknown instead of rerunning; deduplicated job type survives repeated activity delivery. |
| D08 | Host-loss recovery; depends on D04–D07. | Lost local disk, fenced former owner, off-host restore, orphan cleanup, and pinned environment recreation. |
| D09 | One application checkpoint protocol; depends on D05. | Checkpoint integrity/version checks, interrupted publication, and measured restoration of that application only. |
| D10 | Credential/data-flow and operational release gate; depends on the selected implementation scope. | No sandbox control credentials; sensitive payload/history audit, restore drills, operator runbooks, and retention checks. |

These proposals reuse the current
[workflow payload boundary](../../boxie/src/boxie/temporal/contracts.py) and pinned
Python SDK 1.34.0. A Rust Temporal SDK migration is unnecessary for this starting
design. Research was checked against repository contracts and upstream Temporal
documentation on 2026-10-22; no recovery experiment or production rollout is
claimed here.
