# GNU and BSD compatibility

[Project overview](../README.md) · [Architecture](architecture.md)

The target is the combined GNU/BSD feature surface of the existing utilities,
including semantics, not merely acceptance of short and long flags. Current
implementations remain partial. This policy does not certify any utility or add
new flags, compatibility modes, or supported platforms.

## Name the references

GNU and BSD are families, not interchangeable executables. Each utility's first
compatibility audit must record exact targets: GNU program/package version and
named BSD implementation plus OS release or source revision. macOS, FreeBSD,
OpenBSD, and NetBSD results are separate evidence, not substitutes for one another.
Expand the named target set deliberately; do not claim all BSD variants from one
macOS run. Record executable paths so shell builtins, aliases, BusyBox, or uutils
cannot silently replace the intended reference.

Use the appropriate upstream family: GNU Coreutils for its commands, GNU Grep for
`grep`, GNU Findutils for `find`, and util-linux or named BSD implementations for
`cal`. `biggie`, `parsu`, and `kara` have no direct GNU/BSD counterpart: document
their own contracts and meaningful baselines instead of inventing parity claims.
POSIX is useful common ground, not a substitute for GNU/BSD extension coverage.

## Resolve incompatible meanings explicitly

Some flags cannot have both reference meanings in one default invocation. For
example, GNU `ls -G` suppresses the group column in long output, whereas FreeBSD
`ls -G` enables color. See the
[GNU manual, `-G`](https://www.gnu.org/software/coreutils/manual/coreutils.html#ls-G)
and [FreeBSD manual](https://man.freebsd.org/cgi/man.cgi?query=ls&sektion=1).

For each conflict, record both meanings, affected combinations, the chosen default,
and how the other behavior will be exposed. An explicit compatibility policy or
unambiguous option may be appropriate; no global mechanism is selected by this
policy. Preserve the current default until a reviewed per-utility decision changes
it. Do not silently switch semantics at compile time based on the host OS, infer
GNU behavior from Linux alone, or document an unimplemented `--compat` flag.
Library options should express the resolved operation, with an explicit policy
value only where execution genuinely depends on it.

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
| Current status | `verified`, `partial`, `missing`, `divergent`, or `not audited`, separately for each target. |
| Evidence | Test names/fixtures and reference-capture provenance. |
| Resolution | Remaining limitation, intentional difference, or follow-up decision. |

`verified` means tested against the named reference for the stated cases, not
proof of every input. `partial` means some required cases are missing; `divergent`
means a known difference, which still needs explanation. `not audited` is unknown,
not a pass or an automatic failure. Do not publish percentage coverage without a
defined inventory/denominator and treatment of interactions and platform limits.

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

Include empty inputs, no final newline, CRLF, NUL bytes, invalid UTF-8, long records,
non-UTF-8 paths, Unicode and locale boundaries, and overflow/underflow where
relevant. Use byte-based comparisons for byte-based contracts. Test locale-sensitive
behavior separately rather than making every test pass by forcing the C locale.
Filesystem support is an implementation capability, not just a parsing decision.

## Reference and regression workflow

Start with a minimal case and run the chosen reference(s) in a temporary sandbox.
Capture argv, executable identity, version/release, environment, cwd, input bytes,
status, stdout, stderr, and relevant filesystem state. Use fixed locale/timezone
and timestamps except when those are the behavior under test. Avoid privileged
or host-mutating tests; make permission tests explicit about root-user limitations.

Convert confirmed behavior into small hermetic Rust tests and reviewed fixtures.
Tests must normally run without installed reference tools or Nushell. Use the
existing `mk-outs.nu` scripts only for explicit regeneration; record provenance
and inspect differences instead of accepting generated output blindly.
Do not copy incompatible upstream source/tests into this MIT workspace.

Direct library tests verify domain semantics; CLI tests verify the whole process
contract. Where implementations differ, keep separately named expectations and
test the selected resolution. Normalize only irrelevant differences such as the
program name or sandbox path, with an explicit rule; do not trim, sort, or discard
output in ways that hide a real compatibility defect.

Live differential testing is a separate opt-in workflow, not an implemented shared
harness today. A future harness should use explicit executable paths, safe temporary
directories, timeouts for hanging/follow cases, and reproducible case metadata.
Linux/GNU, macOS-native, and any claimed BSD OS need their own validation environments;
missing references are reported as unavailable, never replaced by a different tool.

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
