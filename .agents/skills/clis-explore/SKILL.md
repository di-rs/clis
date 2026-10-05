---
name: clis-explore
description: Use when specifying or bootstrapping work on a utility in the clis workspace, including reference research, unresolved product choices, and planning its migration to the CLI standard.
---

# Explore a utility

Prepare an evidence-backed utility contract and a concrete next implementation
slice. This flow establishes what to build; a request to explore alone does not
authorize implementation, fixture replacement, commits, or PR publication.

## Establish scope

Read `AGENTS.md`, the app README, manifest, affected implementation/tests, and any
nearer guidance. Read the [north star](../../../docs/north-star.md) and
[utility template](../../../docs/templates/utility.md). They own requirements and
the output structure. Reuse existing contracts, handoffs, and decisions; compare
their recorded revision with the checkout and inspect changed files or unresolved
claims instead of restarting a completed review.

Identify the useful domain operations and whether the app is a port or custom
tool. Inspect available execution environments; the OS names in a CI file are not
proof that validation ran or that another local platform is unavailable.

## Research and resolve

For ports, inventory the versioned GNU baseline and review named BSD implementations
for useful additions, following [compatibility](../../../docs/compatibility.md).
Keep GNU requirements, accepted additions, and undecided candidates distinct.
Native macOS utilities are possible research references, not mandatory baselines.
Custom tools define their own contract; mark port requirements inapplicable with
reasons rather than inventing a GNU equivalent.

Separate product choices from technical unknowns:

| Unknown | Next action |
| --- | --- |
| Desired BSD feature, conflicting spelling, presentation choice, acceptable trade-off | Ask the user if session decisions do not already settle it. |
| What the named reference does on an input | Read its manual and run a small isolated probe when available. |
| Missing reference/platform/tool | Record the limitation and continue independent work. |

Ask focused questions before assuming product choices. Research can continue while
answers are pending; keep dependent proposals visibly unresolved. Do not ask again
about an accepted decision. Use actual reference identity and scoped capture evidence;
do not substitute whatever command happens to be on PATH.

Apply U1–U9 even for a narrow proposed implementation slice. Record unaudited areas
as unverified. Include library boundaries, byte/Unicode semantics, terminal usability,
the U6 design checklist, platform evidence, and the [U9 observability contract](../../../docs/observability.md). GNU TTY defaults are parity work;
presentation beyond them is opt-in. A declined pager can be a reasoned decision,
not a missing implementation of a universal pager requirement.

## Deliver the contract

Return or update the requested artifact using the template, without overwriting
unrelated README content. Produce:

1. Current scope/revision and concise findings, with evidence links and limitations.
2. The utility contract, including all requirement IDs, intended/current behavior,
   reference/BSD decisions, and unresolved product questions.
3. An ordered next slice: reference cases, failing regressions, domain API/change,
   CLI adaptation, documentation, and verification/benchmark acceptance.

Link procedures instead of restating the standard. Label proposed APIs and unrun
examples. For a port, include `mk-outs.nu`, expected-fixture provenance, and a
correctness-checked benchmark plan; these artifacts may be missing today.

For example, exploring `lsr` can identify GNU ordering as required, record a BSD
color option as a candidate with a spelling conflict, and ask which opt-in display
extension is wanted. It does not silently enable a new pager or claim the existing
tests prove parity.

When implementation is already authorized, continue with the resolved slice using
the regression and verification skills. Pending product decisions constrain only
dependent work; exploration is not a new blanket approval gate.
