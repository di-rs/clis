---
name: clis-audit
description: Use when assessing a utility in the clis workspace against the CLI standard, reviewing migration readiness, or identifying compatibility, library, testing, documentation, and performance evidence gaps.
---

# Audit a utility

Produce a scoped evidence report and actionable follow-ups. An audit alone is
read-only for repository content: it does not authorize repairs, fixture replacement,
commits, or publication. Update a contract only when the task requests that artifact.

## Establish the evidence boundary

Read `AGENTS.md`, the [north star](../../../docs/north-star.md), and the app's contract.
Use the [utility template](../../../docs/templates/utility.md) for an absent or
incomplete record. Identify port/custom scope, candidate revision and dirty changes,
requested claims, and prior evidence. Reuse unchanged findings after comparing the
recorded base; recheck changed files and unresolved claims. A full audit covers
U1–U9 and applicable P1–P4; a focused audit labels the remaining scope unassessed.

Inspect source, public APIs, tests, reference provenance, docs, and benchmark
artifacts relevant to those claims. Treat implementation inspection, existing
test expectations, fresh execution, and historical results as distinct evidence.
For each platform, record what environment is actually available; a CI configuration
alone proves neither a passing run nor lack of local macOS access.

## Test claims against evidence

Use the north star's status definitions and owning engineering guides. Keep
adoption decisions separate from compliance: an undecided BSD candidate is not a
required feature; a deferred GNU requirement still leaves a gap.

| Claim | Evidence to inspect |
| --- | --- |
| GNU parity | Versioned option/behavior inventory, actual reference identity, captured cases, CLI assertions, platform limits. |
| BSD additions | Named feature review, adoption/conflict decisions, evidence for accepted behavior. |
| Library reuse | Direct typed operations used by CLI and Rust callers, explicit I/O/errors, direct tests, compiled consumer example. |
| Text and usability | U4 cases, TTY/pipe behavior, presentation decisions and U6 review. |
| Performance | Correctness gates, equivalent work, priority cases, previous/reference identities, raw results, variance, memory and accepted regressions. |
| Observability | Typed errors, diagnostic/status mapping, level precedence, sinks, stage spans, flush failures and overhead; see [observability](../../../docs/observability.md). |

Run relevant checks through [clis-verify](../clis-verify/SKILL.md) when execution is
within scope. Use controlled probes and fixture guidance from
[compatibility](../../../docs/compatibility.md#reference-and-regression-workflow)
for unresolved behavior. Missing tools/references remain explicit limitations;
native macOS commands cannot silently stand in for GNU. Existing tests can preserve
incompatibilities, and an existing binary without revision identity is weak evidence.
Do not replace expectations or suppress a failure to produce a clean audit.

## Deliver the assessment

Lead with readiness for the requested claims and the most consequential gaps.
Then provide a concise table: requirement ID, scope/platform, evidence status,
evidence links or exact executed command/results, and remaining work. Split a row
when Linux and macOS evidence differs. Link detailed contract sections rather than
copying them. Every required claim needs support; a green suite is not certification.

Finish with ordered, concrete follow-ups and the checks not run. Distinguish observed
defects, missing implementation, missing evidence, and unresolved product choices.
For each defect give a reproducer or source/test location and an acceptance test;
for unknown behavior give a reference probe. Explain inapplicable requirements.

For example, a `catr` audit may find passing CLI tests that require success after
a missing operand, while GNU evidence is absent. Report the expectation and the
needed reference/status regression; do not bless it as parity or change it inside
the audit. A missing benchmark recipe is `missing`; speed remains unverified.

End after the requested assessment or contract update. Continue implementation
only when already authorized for the identified scope; otherwise report follow-ups.
