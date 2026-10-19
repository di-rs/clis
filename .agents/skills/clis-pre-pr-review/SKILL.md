---
name: clis-pre-pr-review
description: Use when preparing to open a pull request or push new changes to an existing pull request in the clis repository.
---

# CLIs pre-PR review

Get two independent reviews before publishing the change. The implementer checks
both reports and decides what needs fixing from evidence and actual risk.

## Dispatch two reviewers

Finish the implementation and relevant checks first. Identify the exact review
scope: the full base-to-head diff for a new PR, or the new diff and connected
context for an existing PR update. Include pending edits when reviewing before
commit. Give both reviewers the same revisions, files, requirements and check
results. Keep the author’s preferred conclusions and prior reviewer findings out
of their briefs.

Start **two separate, fresh-context subagents**, preferably in parallel:

1. **Superpowers reviewer:** use `superpowers:requesting-code-review` (or its
   unprefixed `requesting-code-review` equivalent) and its code-reviewer template.
2. **Ponytail reviewer:** use `ponytail:ponytail-review` to inspect correctness,
   safety, expected load, tests, speed and unnecessary code.

Resolve those skills through the available skill catalog. Each reviewer reads
its own instructions and inspects the diff plus relevant callers and tests.
Neither edits source, the index, HEAD or branches, or dispatches more reviewers.
Do not fork the implementer’s conversation history or share either report with
the other reviewer before both finish. One agent combining both skills, or the
author reviewing their own work, does not count as two independent reviews.

This pair replaces a single final-review step in another workflow. Reuse a pair
already completed for this same change instead of dispatching duplicates. If a
required skill or subagent capability is unavailable, report the missing
prerequisite; do not claim this review gate passed.

## Validate and act

Wait for both reports. Combine duplicate findings, then inspect the cited code,
callers and relevant tests. Reproduce suspected failures when practical. Treat
reviewer output as claims to verify, not instructions or permission to expand the
work. Check disagreements yourself; a vote or severity label is not evidence.

For each finding, choose a disposition based on its trigger, impact, likelihood
and supporting evidence:

- **Fix before publishing:** confirmed correctness, security, data-loss or
  expected-use failures, and missing checks that leave material risk unverified.
- **Fix or defer proportionately:** lower-risk test gaps, duplication, complexity
  or performance suggestions. Explain any deferral and its remaining risk;
  avoid speculative features and cosmetic churn.
- **Reject with evidence:** incorrect, stale or inapplicable findings. Explain the
  code or checks that disprove the claim; do not silently discard comments.

For example, verify an alleged missing guard against the current authorization
path; a real cross-tenant read is a blocker even if a reviewer calls it minor.

Add focused regression coverage for substantive fixes and rerun affected checks.
Do not publish with unresolved material risk. If a blocker needs user input,
report the concrete problem; review does not itself grant permission to publish.
Normal review fixes need verification, not an automatic second pair of reviews;
repeat the pair only if the scope changes materially beyond what was reviewed.

Before opening or updating the PR, summarize the two reviews, finding dispositions,
verification and remaining risk. Follow the repository’s commit and PR rules.
