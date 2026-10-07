# CLI north star

[Project overview](../README.md) · [Utility template](templates/utility.md)

Build readable, predictable Unix-style commands whose useful operations are also
available directly to Rust applications. Prioritize GNU behavior parity in existing
ports, then measured performance improvements, while preserving correctness and
maintainable library boundaries throughout.

This document defines the destination. Current utilities do not yet meet every
requirement. Establish the standard first, then migrate one utility at a time;
publishing it does not certify the workspace or implement features. Linux and macOS
are the supported runtime targets. BSD implementations are also reviewed for useful
features, independently of the supported operating systems.

## How to use this standard

Use the [template](templates/utility.md) to maintain a utility's contract in its
README, linking out large compatibility tables or benchmark records. Record intended
behavior and current evidence separately. Preserve existing credits.

Requirement IDs below are stable references for reviews and audits. The engineering
guides own implementation procedures; this document owns acceptance criteria.
Skills help carry out that work without defining another standard.

## Every utility

### U1 — Reusable Rust operations

CLI and Rust callers use the same domain implementation. Public APIs accept typed
options and explicit resources, validate direct callers, and return useful outcomes
and errors. Process setup and policy stay in the adapter. Evidence includes direct
API tests and a compiled consumer example. See [architecture](architecture.md).

### U2 — Consistent tests

Use CLI tests,
direct library tests, and small reusable fixtures. Assert observable results and
failure paths, including relevant byte boundaries and read/write failures. Ordinary
tests are hermetic; reference capture and timing are explicit separate workflows.

### U3 — Useful documentation

Each README explains purpose, examples, every implemented flag and operand,
defaults, interactions, limitations, and library use. Ports also inventory required
reference behavior and missing flags. Label planned APIs and features clearly;
examples presented as working must be checked. Keep long reference tables in a
linked file when needed, with one authoritative location for each fact.

### U4 — Text, bytes, and formatting

Define whether each operation uses bytes, Unicode scalar values, grapheme clusters,
or terminal display columns. Test relevant boundaries: multibyte text, combining
marks, wide characters, emoji sequences, tabs, CRLF, NUL, ANSI/control sequences,
invalid UTF-8, non-UTF-8 paths, and missing final newlines. Record reasons for
inapplicable cases. Preserve bytes where the contract requires them; Unicode support
does not authorize normalization, replacement, or stripping formatting from data.

### U5 — Terminal usability

Every utility assesses readable terminal output: columns/tables, width handling,
color, symbols, and paging. Record each decision as implemented, planned, deferred,
or inapplicable, with a reason and any required user decision. A command with no
displayable data can satisfy this assessment without inventing a presentation mode.

For ports, GNU's own TTY behavior remains part of parity. Enhancements beyond it
are opt-in and preserve ordinary invocation and pipeline output. Define behavior
for TTY, redirected streams, narrow terminals, disabled styling, and absent pagers.
Test selected modes and fallbacks; terminal capability detection belongs outside
domain operations.

### U6 — CLI design review

Review the [Command Line Interface Guidelines](https://clig.dev/) per utility.
Record applicable decisions, gaps, and justified exceptions using this checklist:

| Area | Review |
| --- | --- |
| Help and documentation | Discovery, examples, version information, support path. |
| Output and errors | Stream separation, actionable diagnostics, machine consumption. |
| Arguments and subcommands | Naming, defaults, validation, consistency. |
| Interactivity | Noninteractive operation, prompt policy, cancellation. |
| Robustness and signals | Partial failure, interruption, recovery, bounded waits. |
| Configuration and environment | Precedence, locations, variables, sensitive values. |
| Future compatibility and distribution | Interface changes, dependencies, installation, upgrades. |

GNU behavior governs ports when a guideline conflicts, including help flags,
stdin waiting, output, and exit codes. Record the exception instead of silently
changing compatibility. Full-screen apps such as Kara need their own interaction
review; apply relevant principles without imposing a line-oriented UI.

### U7 — Platform evidence

Validate applicable behavior on Linux and macOS, recording versions, architecture,
filesystem/locale constraints, and outcomes separately. Document unavailable OS
capabilities as limitations. Neither a platform-skipped test nor a Linux container
establishes native macOS behavior. Current CI is Linux-only; this standard does not
add a macOS job or claim either platform is fully certified.

### U8 — Maintainable implementation

Use focused modules, narrow APIs, workspace conventions, explicit resource bounds,
and shared mechanisms for demonstrated consumers. Review error paths and API
compatibility. Optimize measured bottlenecks with correctness evidence and record
trade-offs. Follow [architecture](architecture.md#readability-and-performance) and
[benchmarking](benchmarking.md).

### U9 — Errors and observability

Use typed domain errors and anyhow at the CLI boundary. Required diagnostics remain
independent of optional logging. Use one shared CLI setup with `--log-level`, text
on stderr, and automatic tracing installation; debug/trace enables stage timings.
Preserve GNU flags and data streams. Libraries emit log events and tracing spans
without initializing a collector.
Check sink/flush failures and finish owned resources before exit. Test the boundaries
and measure instrumentation overhead; follow [observability](observability.md).

## Additional requirements for ports

### P1 — GNU baseline

Target full option and behavior parity with a recorded GNU release: Coreutils for
its utilities, GNU Grep for `grep`, and GNU Findutils for `find`. For a command with
no GNU counterpart, select a concrete reference; `calr` currently points to
util-linux `cal`. A newer release requires an explicit inventory update. Flags,
defaults, interactions, errors, statuses, streams, and filesystem effects all count.
Full parity remains a goal while required behavior is missing or unknown. See
[compatibility](compatibility.md) for evidence and platform limitations.

### P2 — BSD feature review

Review named BSD implementations for valuable additional flags and behavior.
Record source/version, usefulness, conflicts, and adoption decisions. Adoption is
selective; accepted features become requirements with their own tests and evidence.
Preserve GNU meanings for existing spellings. Resolve conflicting additions per
utility in its README, using an unambiguous interface agreed before implementation.
The review does not require full parity with every BSD or a BSD runtime target.

### P3 — Reference fixtures

Each port supplies `mk-outs.nu` and `tests/expected/`, with inputs and provenance
for a reviewed case matrix. Capture the actual reference's stdout, stderr, status,
and relevant filesystem effects. Cover defaults, flags, interactions, boundaries,
and errors; grow coverage as unknowns are investigated. This is a finite inventory,
not every possible input or output. Follow the
[reference workflow](compatibility.md#reference-and-regression-workflow).

### P4 — Correctness-checked benchmarks

Each port has a reproducible benchmark recipe covering its priority workloads and
relevant startup, throughput, streaming, flags, and memory cases. Compare equivalent
results before timing against GNU (or the selected reference) and the previous Rust
revision. Compare adopted BSD-only behavior to its named reference where applicable.
Use `biggie` for large text where suitable; preserve generated input and its checksum.

The target is to beat references on declared priority workloads. Record every
measured regression and require explicit acceptance of its trade-off; a regression
does not disappear because another case wins. Report ties/noise as inconclusive.
Rust alone is no evidence of speed. See [benchmarking](benchmarking.md) for procedure.

## Evidence and adoption

Use these statuses for requirements and behavior rows:

| Status | Meaning |
| --- | --- |
| `verified` | Evidence meets the requirement for the stated revision, platform, and cases. |
| `partial` | Some required implementation or evidence exists; identify what remains. |
| `missing` | Required capability or artifact is absent. |
| `divergent` | Observed behavior differs from the chosen contract. |
| `unverified` | Insufficient inspection or execution evidence to decide. |
| `inapplicable` | Requirement does not apply, with a concrete reason. |

Keep adoption decisions (planned/deferred/declined) separate from evidence status.
Deferring a required GNU flag leaves a gap. An unavailable reference leaves execution
unverified. A justified platform limitation remains visible, not grounds for an
unqualified full-parity claim. Custom apps apply U1–U9 and explicitly mark P1–P4
inapplicable unless they adopt a reference contract.

An audit records revision and scope, then maps each applicable ID to source/test
evidence, commands actually run, results, and remaining work. Passing legacy tests
alone cannot establish parity, library reuse, or performance. Small changes can
land with disclosed gaps; claiming compliance requires all applicable requirements
verified for the declared scope, with exceptions prominent. Migrate each utility
through a contract, failing regressions, library/CLI changes, platform validation,
and updated evidence. A documentation audit does not authorize implementation fixes.
