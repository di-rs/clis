# Verify shish behavior

Run package-owned checks from the repository root:

```sh
cargo fmt -p shish -p true -p false --check
cargo clippy -p shish -p true -p false --all-targets
cargo nextest run -p shish -p true -p false --profile ci
cargo test -p true -p false --doc
uv run cli-check -p shish -p true -p false
```

Normal tests assert exact status and both output streams; filesystem cases use
isolated temporary directories. Diagnostics containing temporary absolute paths
use narrowly scoped predicates. [The shared command helper](../tests/support/mod.rs)
clears inherited environment and sets locale, PATH, HOME, and cwd explicitly.
Older foundation tests use repository defaults with case-specific overrides.

The [PTY driver](../tests/support/pty.rs) starts the built CLI in an isolated
terminal and temporary home, renders terminal escape sequences with vt100, and
answers cursor-position queries. Each expected observation and exit has a
six-second deadline; terminal output is capped at 1 MiB. PTY output is necessarily
one combined stream; stream separation is asserted in noninteractive tests.
The driver tracks only its shell's descendants, checks process start identity,
kills surviving descendants on drop, and reaps its shell. A regression verifies
cleanup while a child is still running. Tests do not inspect unrelated process
arguments or write to the user's history. Use short-lived jobs and explicit waits
in addition to the cleanup guard.

The initial PTY audit verifies input/EOF and foreground Ctrl-C/status 130. It does
not prove every completion, history, job-control, or terminal-resize behavior.
Each feature has its own focused case. Assertions should distinguish
executed output from echoed input and should inspect the rendered screen when
cursor movement matters. Wait for `expect_line("shish-test> ")` between commands;
a substring match can see an old prompt, and the input backend can discard input
queued beyond the command it has just returned.

## Deferred acceptance cases

P01–P04 and P06 in the [compatibility inventory](compatibility.md#confirmed-posix-gaps)
and C25/C28/C32/C34/C40/C44/J54/H68 in the interactive gap section are explicit
ignored tests asserting the required behavior. They are known to
fail; two more ignored cases cover [multiline input and PATH lookup](compatibility.md#input-and-command-lookup-gaps).
Ignored cases are never counted as verified support. To reproduce them:

```sh
cargo nextest run -p shish --profile ci --run-ignored only
```

A future focused fix must make its case pass, remove its ignore annotation, and
update the inventory. Do not replace the expected result with the current defect
or claim a green default suite establishes conformance.

## Independent expectations

The [compatibility reference](compatibility.md#reading-the-evidence) records the
audit environments and reference shell. Omit `--posix` for Bash-extension checks.
Record executable/version/mode when adding another reference.
Specification-derived expectations take precedence over reference behavior where
the reference predates POSIX.1-2024. No snapshot generator uses shish as its oracle.

Repository CI runs workspace tests; local work runs affected packages only. The PTY test helpers
use portable-pty 0.9.0 (which currently brings nix 0.28.0), sysinfo 0.37.2, and
vt100 0.16.2. The pinned nightly reports a future-compatibility warning in that
transitive nix version; current compilation and Clippy pass. Track an upstream
helper release before upgrading Rust past the warning's compatibility boundary.
