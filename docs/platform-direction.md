# Sanctuary platform direction

Sanctuary is being built as a platform for executing durable shell sandboxes.
Its runtime, shell, and consumer services have distinct responsibilities, so new
applications can use the platform without inheriting the CLI playground's product
model.

## Platform responsibilities

Boxie is the platform runtime. It owns sandbox execution and isolation. The shell
will be a builtin in this repository, with the repository's other builtins baked
into its environment. The shell is work in progress and does not exist yet.

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

## Open design question: shell durability

The shell needs a Temporal-backed execution design, but the division of work
between shell and runtime remains open. Two approaches deserve evaluation:

| Approach | Benefit | Design work it requires |
| --- | --- | --- |
| Shell-owned workflows and activities | Shell operations can express their own durable execution semantics. | Define replay-safe operations, side-effect handling, credentials, and how shell workers recover. |
| Runtime-mediated durable execution | Boxie can keep workflow connectivity and credentials outside the shell environment. | Define how the shell expresses resumable work and how runtime recovery preserves shell semantics. |

Temporal service placement is a separate choice. A shared service outside
sandboxes centralizes operations but needs an explicit trust and isolation model.
A service inside each sandbox would couple its storage and recovery to the sandbox
and add per-environment operating cost. Neither placement is selected here.

Before choosing, define what survives failure: command progress, filesystem state,
shell variables, external side effects, and terminal connections are different
contracts. Also decide how cancellation, retries, environment versions, and consumer
ownership interact. Existing lifecycle recovery is not evidence for any stronger
shell guarantee.

These questions guide future design work; this decision does not prescribe a
runtime migration schedule or new public API.
