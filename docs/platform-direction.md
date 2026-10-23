# Sanctuary platform direction

Sanctuary is being built as a platform for executing durable shell sandboxes.
Its runtime, shell, and consumer services have distinct responsibilities, so new
applications can use the platform without inheriting the CLI playground's product
model.

## Platform responsibilities

Boxie is the platform runtime. It owns sandbox execution and isolation. Shish
is a shell application in this repository. It embeds Brush for local shell
execution; compatibility verification and durable-execution integration remain
separate work.

Separate applications and services will manage consumer accounts, consumer API
tokens, and environment setup. Their names, interfaces, and deployment boundaries
are not settled. Runtime enforcement will still need to honor the consumer and
execution policies established through those services.

CLI playground is an internal tool and Sanctuary's first consumer. Its job is to
let people try repository commands in a browser. As the platform develops, it
will use the same consumer boundary as other applications rather than determine
Sanctuary's runtime or account model.

## Where the implementation stands

Today, Boxie exposes a private consumer API and runs bounded Bash sandboxes.
Temporal workers in its supervisor coordinate provisioning and cleanup; they do
not make shell processes or terminal sessions resumable after a supervisor restart.
The playground connects directly to that API, uploads preview binaries, and owns
its own preview and browser-session metadata.

The [current architecture](architecture.md) describes these implemented boundaries.
The target platform does not change today's operating requirements or imply that
the future shell and management services are available.

## Compatibility and product priorities

The platform's goal is isolated shell workloads with explicit, tested recovery
guarantees. Four contracts contribute to that goal:

| Contract | Responsibility |
| --- | --- |
| Shell compatibility | Predictable arguments, quoting, expansion, pipelines, redirection, and exit statuses |
| Sandbox isolation | Enforced filesystem, network, process, resource, and ownership boundaries |
| Execution durability | Defined outcomes after disconnects, crashes, cancellation, and retries |
| Interactive experience | Prompts, completion, history, and job controls |

POSIX is a compatibility reference for the shell. Full conformance is not a
prerequisite for platform delivery. Prioritize correct common shell operations and
clear recovery semantics; retain the complete shell-feature roadmap without making
every interactive feature or compatibility edge case a platform release gate.

Shish embeds Brush and tracks the behavior verified through shish itself. Parser and runtime integration stay inside the shish package
until another component needs a shared API. Selecting a library or enabling its
POSIX mode does not establish conformance.

Shish's package documentation will own the detailed compatibility inventory.
Record verified, partial, missing, and unverified behavior against the tested
library version, shell mode, and platform. For a gap, retain the applicable
standard section, a reproducer, expected and observed results, its cause and user
impact, any workaround, and test and follow-up links. Track Bash extensions
separately. Upstream-reported limitations require reproduction before they become
confirmed shish defects.

If strict POSIX conformance becomes a product requirement, explicitly select the
standard edition, shell and builtin scope, and supported platforms. Expand the
test matrix and make the relevant recorded gaps acceptance criteria. Shell
compatibility alone makes no operating-system-wide or complete utility-conformance
claim.

## Shell durability direction

Start with shish inside Boxie's existing operator-controlled image/profile,
admission, supervisor, terminal, and Temporal lifecycle. The
[shish durability design](../packages/shish/docs/durability.md) recommends
runtime-mediated durable jobs as a later, explicitly submitted workload type.
Keep Temporal workers and credentials outside sandboxes. The design compares
shell-owned workers and other alternatives and defines focused implementation
follow-ups; it introduces no remote execution API or recovery implementation.

Reconnecting to a surviving process, restarting an entire job, and resuming from a
checkpoint are different guarantees. Ordinary background jobs do not implicitly
become durable workflows. An AST describes shell syntax; it does not establish safe
checkpoint or replay boundaries.

For example, a command that appends to a file may finish its write before a worker
crashes without recording success. Retrying can duplicate the write. The durable
execution contract must define retry eligibility and uncertain outcomes; arbitrary
shell side effects cannot be assumed idempotent.

Files, shell variables, output, command progress, and external effects each need
explicit persistence and recovery contracts. Existing lifecycle recovery is not
evidence for any stronger shell guarantee. Durable jobs and process checkpointing
remain future product decisions with their own acceptance criteria.
