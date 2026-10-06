# Utility contract template

[North star](../north-star.md) · [Compatibility](../compatibility.md) ·
[Architecture](../architecture.md) · [Benchmarking](../benchmarking.md)

Use this structure in an app README. Replace bracketed prompts, adjust links for
the app's location, and retain attribution. Link out large tables instead of copying
them. This is not an implemented utility or completed audit. Use the north star's
evidence statuses; record unknowns instead of assumptions. Custom apps explain why
port-only fields are inapplicable and retain the universal assessments.

---

# [Utility name]

[Operation, intended users, important constraints. Link to the workspace README
and the north star.]

## Status and scope

- Package/binary names: [actual names, including multiple binaries].
- Reviewed revision/date: [commit, dirty diff if any, date].
- Current capability: [factual summary; distinguish target from implementation].
- Linux/macOS: [separate versions, architectures, constraints, evidence links].
- Scope: [audited surface; state what remains unaudited].

## Quick start

[Give a minimal invocation and a useful composition example. State the working
directory, build/install prerequisites, stdin behavior, and file effects. Check
stdout/stderr/status. Label illustrative or unrun examples.]

## Flags and behavior

[Inventory every implemented flag, alias, operand, default, and required reference
feature. Include parser interactions, environment, statuses, errors, and partial
effects. Give testable behavior, not a copy of help output. State how the inventory's
scope was checked; link large tables to a single maintained file.]

| Flag, operand, or behavior | Required meaning and interactions | Current status and limitation | Evidence |
| --- | --- | --- | --- |
| [Default invocation] | [Input/output/defaults] | [Status; platform scope] | [Tests/manual section] |
| [Flag and aliases] | [Values, precedence, errors] | [Status; gap] | [Tests/fixtures] |

## References and extensions

| Role | Implementation and version | Executable/platform or manual | Coverage |
| --- | --- | --- | --- |
| Baseline | [GNU package/release or selected alternative] | [Path, OS, manual sections] | [Observed cases or manual-only] |
| BSD review | [Named implementation/release] | [Manual/executable identity] | [Feature inventory reviewed] |

| BSD candidate | Value to users | GNU conflict and proposed interface | Decision and evidence |
| --- | --- | --- | --- |
| [Feature] | [Concrete use] | [Preserve GNU meaning; unresolved choice] | [Accepted/deferred/declined; rationale, tests/status] |

[Record unresolved questions, platform-dependent behavior, and intentional
differences. A manual lookup is not an executed comparison.]

## Rust library

[Show an actual dependency declaration and a direct-call example that compiles.
Describe public types, caller-owned resources, errors/partial outcomes, side effects,
state across calls, flushing, and memory bounds. Link API docs and direct tests.
If unavailable, mark U1 missing/partial and describe the proposed boundary separately;
do not publish planned APIs as working examples.]

## Errors and observability

[Record domain error types, diagnostic/status mapping, shared log-level integration,
CLI/environment precedence and verbosity conflicts. Identify stage spans/counts,
flush and sink-failure behavior, privacy choices, instrumentation overhead evidence,
and remaining U9 gaps. Follow the [shared contract](../observability.md).]

## Text and terminal behavior

| Input or presentation case | Contract and applicability | Status/evidence |
| --- | --- | --- |
| Bytes, scalars, graphemes, display width | [Unit for each operation] | [Cases/tests] |
| Combining marks, wide text, emoji sequences | [Preservation/layout] | [Cases/tests] |
| Tabs, CRLF, NUL, control/ANSI sequences, missing final newline | [Exact handling] | [Cases/tests] |
| Invalid UTF-8 and non-UTF-8 paths | [Preserve/reject per contract] | [Cases/tests] |
| TTY versus pipe/file output | [Reference defaults and opt-in extensions] | [Cases/tests] |
| Tables/columns, color/symbols, pager | [Implemented/planned/deferred/inapplicable, reason] | [Modes/fallback checks] |
| Narrow terminal, disabled styling, unavailable pager | [Fallbacks and precedence] | [Cases/tests] |

## CLI design assessment

[Use U6's checklist: help/documentation, output/errors, arguments/subcommands,
interactivity, robustness/signals, configuration/environment, future compatibility/
distribution. Record decisions, evidence/gaps, or reasons for inapplicability in each
area. Explain GNU-related exceptions. Do not invent JSON, prompts, or config files
merely to fill the checklist.]

## Tests and reference capture

[Link tests/cli.rs, direct API tests, reusable inputs, and expected outputs. Describe
the case matrix and platform coverage, including injected errors where relevant.]

For ports, document `mk-outs.nu`:

- [Exact command, working directory, Nushell version, explicit reference selection].
- [Case IDs, argv/stdin, controlled environment, statuses and side effects].
- [Where stdout/stderr/status and provenance are saved under tests/expected/].
- [How to regenerate safely, review changes, and run hermetic regression tests].
- [Unavailable references, unsupported cases, and remaining capture coverage].

## Benchmarks

[Use the [benchmark report template](benchmark-report.md). Ports: link the checked
recipe and raw results. Define priority workloads before
timing, small/large/streaming/flag/memory cases, candidate/previous/reference identities,
correctness gate, host/environment, generation and checksum, statistic/variance,
regressions and explicit acceptance. Use biggie where suitable; keep large data
outside fixtures. Missing measurements remain unverified. Custom apps document
relevant measurements or why a comparison is inapplicable.]

## Standards evidence and next work

[One row per U1–U9 and, for ports, P1–P4. Split by platform where evidence differs.
Link owning sections above instead of copying detail.]

| Requirement ID | Scope/platform | Evidence status | Evidence and commands actually run | Gap or next step |
| --- | --- | --- | --- | --- |
| [ID] | [Cases/revision/platform] | [Status] | [Links, command, outcome] | [Concrete work or none] |

[List follow-ups in dependency order with a regression or acceptance check. Separate
product questions from technical unknowns that can be probed. Record unrun checks
and unresolved decisions; preserve tutorial/licensing credits.]
