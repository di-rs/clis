# Compatibility and reference evidence

[Project overview](../README.md) · [Architecture](architecture.md)

The [north star](north-star.md#additional-requirements-for-ports) defines the policy:
GNU is the baseline; named BSD implementations supply candidates for useful additions.
This guide explains how to investigate behavior and build evidence for that contract.
Current implementations remain partial.

## Name the references

Record the GNU program/package release and executable path. Use its versioned
manual and observed behavior together; a rolling online manual may describe a newer
release. Record build/platform differences and refresh the inventory deliberately
when upgrading the reference. Shell builtins, aliases, BusyBox, uutils, or macOS's
bundled command must not silently replace the chosen GNU executable.

For BSD feature research, record each named implementation and OS release/source
revision. macOS, FreeBSD, OpenBSD, and NetBSD observations are distinct. A manual-only
review can identify a candidate; executable validation is still needed for an adopted
behavior claim. Review relevant named implementations without requiring every BSD
variant or installing another OS merely to complete an initial exploration.

Use the appropriate upstream family: GNU Coreutils for its commands, GNU Grep for
`grep`, GNU Findutils for `find`, and an explicitly selected reference for `cal`
(currently util-linux in `calr`'s README). `biggie`, `parsu`, and `kara` have no direct
GNU/BSD counterpart: document
their own contracts and meaningful baselines instead of inventing parity claims.
POSIX is useful context, not a replacement for the chosen command's contract.

## Resolve incompatible meanings explicitly

Some flags cannot have both reference meanings in one default invocation. For
example, GNU `ls -G` suppresses the group column in long output, whereas FreeBSD
`ls -G` enables color. See the
[GNU manual, `-G`](https://www.gnu.org/software/coreutils/manual/coreutils.html#ls-G)
and [FreeBSD manual](https://man.freebsd.org/cgi/man.cgi?query=ls&sektion=1).

Keep GNU's meaning for the GNU spelling. For a useful BSD addition, record its
benefit, both meanings, affected combinations, and the proposed unambiguous interface
in the app README. Ask the user to resolve unsettled product choices before adding
it; a deferred or declined candidate is a valid review outcome. Do not introduce a
global compatibility mode or switch meanings by host OS. Library options express
the resolved operation rather than the conflicting spellings.

When today's Rust default differs from GNU, record it as a migration gap. Change it
through a scoped, documented behavior fix with regression evidence; a documentation
edit does not silently change existing code or tests. GNU semantics are the target
on both Linux and macOS. OS-dependent capabilities and diagnostics need explicit
platform cases and limitations, not substitution of native BSD semantics.

## Per-utility compatibility record

Maintain the record in the app README, or a linked `COMPATIBILITY.md` when it grows.
Inventory the entire declared reference surface before claiming completeness,
including defaults and operand syntax. For incremental work, inventory and update
the touched behavior first and explicitly mark the remaining surface unaudited.

Each behavior row must record:

| Field | Required content |
| --- | --- |
| Feature | Flag spellings, operands, default behavior, or an interaction. |
| References | Manual sections and exact GNU/BSD versions or OS releases. |
| Expected semantics | Meaning, precedence, output, errors, and side effects. |
| Current status | A [north-star evidence status](north-star.md#evidence-and-adoption), separately for each relevant platform/reference. |
| Evidence | Test names/fixtures and reference-capture provenance. |
| Resolution | Remaining limitation, intentional difference, or follow-up decision. |

Use the [utility template](templates/utility.md) for the record and a separate BSD
candidate/decision table. Adopted additions join the required inventory; merely
reviewed candidates do not. Do not publish percentage coverage without a defined
inventory/denominator and treatment of interactions and platform limits.

## What must match

Test parser behavior as well as execution: short-option clusters, long aliases,
attached/separate values, repeated options, precedence, options after operands,
`--`, operands beginning with `-`, stdin sentinels, count/range boundaries, and
invalid syntax where the reference defines them. Clap defaults are not the spec;
normalization may be needed for reference-specific syntax. Do not reject a valid
combination merely because `conflicts_with` is simpler.

Observable behavior includes exact stdout bytes, documented stderr/diagnostics,
exit status, multi-file ordering and state, continuation after an input failure,
TTY versus pipe behavior, and early pipe closure. For filesystem tools include
permissions, ownership where available, symlinks, timestamps, and partial effects.
Do not apply one generic exit-code or broken-pipe rule to every command without
reference evidence. Preserve useful domain outcomes so adapters can map them.

Apply U4's [text and formatting matrix](north-star.md#u4--text-bytes-and-formatting),
plus empty/long records and numeric overflow/underflow where relevant.
Use byte-based comparisons for byte-based contracts. Test locale-sensitive
behavior separately rather than making every test pass by forcing the C locale.
Filesystem support is an implementation capability, not just a parsing decision.

## Reference and regression workflow

Start with a minimal case and run the chosen reference(s) in a temporary sandbox.
Capture argv, executable identity, version/release, environment, cwd, input bytes,
status, stdout, stderr, and relevant filesystem state. Use fixed locale/timezone
and timestamps except when those are the behavior under test. Avoid privileged
or host-mutating tests; make permission tests explicit about root-user limitations.

Convert confirmed behavior into small hermetic Rust tests and reviewed fixtures.
For each port, provide a package-root `mk-outs.nu` (the repository's existing plural
name). Its README documents the exact invocation, required Nushell version, and
explicit reference executable selection. Require a recognizable implementation/
version or explicit recorded identity; fail on a missing/wrong reference instead
of falling back to PATH. Existing scripts need migration to this contract.

Keep reusable inputs in `tests/inputs/` and captured results in `tests/expected/`.
Use stable case IDs and a human-readable manifest, such as
`tests/expected/README.md`, mapping every case to:

- Reference identity, capture date/platform, command/argv, cwd and environment.
- Input files/stdin and relevant initial filesystem state.
- Exact stdout/stderr files, expected exit status, and resulting filesystem state.
- Any narrowly justified normalization and remaining limitations.

The script creates a disposable sandbox, captures expected nonzero statuses without
masking unexpected failures, and leaves the checkout's input fixtures untouched.
Use timeouts for commands that can wait/follow. Review regenerated diffs before
adopting them; preserve raw capture evidence when normalization is necessary.
Keep platform-specific expectations separate where the contract requires them.
Ordinary tests consume the checked-in small fixtures without references or Nushell.
Do not copy incompatible upstream source/tests into this MIT workspace.

Unit tests beside the implementation verify domain rules; public API integration
tests verify consumer workflows, and CLI integration tests verify the whole process
contract. Where implementations differ, keep separately named expectations and
test the selected resolution. Normalize only irrelevant differences such as the
program name or sandbox path, with an explicit rule; do not trim, sort, or discard
output in ways that hide a real compatibility defect.

Live differential testing is a separate opt-in workflow, not an implemented shared
harness today. A future harness should use explicit executable paths, safe temporary
directories, timeouts for hanging/follow cases, and reproducible case metadata.
Validate the Rust command on Linux and macOS with actual GNU references where
available. Linux containers can provide GNU discovery evidence, not native macOS
validation or comparable cross-host timings. An adopted BSD-only feature needs its
own reference evidence; the research host need not become a supported Rust platform.
Missing references stay unavailable, never replaced by a different tool.

## Completing a compatibility change

A change must include its reference evidence, failing-then-passing regression,
library and CLI implementation where needed, matrix update, and remaining limits.
Existing tests can encode a known divergence: update such an expectation only with
reference evidence and a stated behavior change, not to conceal a regression.
Benchmark the new equivalent behavior rather than comparing a fully featured
reference against a candidate that skips work. Full parity remains a target until
the declared inventory and required platforms have evidence.

## Reference entry points

- [GNU Coreutils](https://www.gnu.org/software/coreutils/manual/coreutils.html),
  [GNU Grep](https://www.gnu.org/software/grep/manual/grep.html), and
  [GNU Findutils](https://www.gnu.org/software/findutils/manual/).
- [FreeBSD manuals](https://man.freebsd.org/),
  [OpenBSD manuals](https://man.openbsd.org/), and the native manual on the exact
  macOS/BSD release under test. Record the release rather than assuming an online
  current manual describes an older installed binary.
- [Uutils compatibility/testing approach](https://uutils.org/coreutils/docs/CONTRIBUTING.html).

These are entry points; each behavior claim still needs a specific manual section
and tested implementation/version in its utility's record.
