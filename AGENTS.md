# Repository guidance

## Mission

Evolve existing utilities toward GNU option and behavior parity, consider useful
BSD additions, and beat reference performance on measured priority workloads.
Correctness, reusable Rust operations, and readable code constrain every change.
These are targets; the current workspace is not certified to meet them.

The [north star](docs/north-star.md) owns requirements U1–U9 and P1–P4. Follow it
and the linked procedures rather than treating existing inconsistencies as patterns
to copy. Root and app READMEs distinguish current support from plans.

## Read for the affected work

- [README](README.md): app index, status, quick start.
- [Architecture](docs/architecture.md): public APIs, boundaries, ownership, sharing.
- [Compatibility](docs/compatibility.md): GNU baseline, BSD additions, reference capture.
- [Observability](docs/observability.md): errors, logging, stage timings, lifecycle.
- [Benchmarking](docs/benchmarking.md): workloads, correctness gates, evidence.
- [Utility template](docs/templates/utility.md): per-app contract and audit record.
- Read the app README, manifest, implementation, tests, and any nearer `AGENTS.md`.

## Working rules

- Prefer one utility or reusable capability per change. State the affected behavior,
  library API, tests, and performance impact before editing. Preserve unrelated work,
  tutorial credits, and consumer contracts; do not modify Kara incidentally.
- Keep added flags, fallbacks, and abstractions justified by the user's intent.
  Remove unnecessary additions from the current change rather than adding more
  machinery to support them; preserve established behavior outside the task.
- Establish the affected baseline. Add a failing regression for a behavior fix,
  implement the domain operation, then connect the CLI adapter. Use typed options
  and explicit resources; CLI and Rust callers share the implementation.
- Resolve product choices before assuming: selected BSD additions, conflicting
  spellings, presentation modes, and intentional behavior changes. Reuse decisions
  already made. Investigate technical unknowns with controlled reference probes.
- Keep GNU meanings across platforms. A listed flag or old passing suite does not
  prove parity. Record reference identity and scope; label unavailable evidence.
- Keep domain code independent of argv, Clap, global streams, process exit, and
  process-wide initialization. Follow the architecture guide for errors and I/O.
- Use `anyhow` for application error handling and `thiserror` for typed errors
  when callers need to match variants and take different actions. Apply this to
  new or changed error handling; keep unrelated migrations out of scope.
- Prioritize end-to-end (E2E) CLI tests for most changes. Use unit tests
  selectively for complex logic or hard-to-reach edge cases; they need not cover
  every implementation detail or duplicate E2E coverage. Retain direct API tests
  for changed library contracts.
- Derive expected results from documented behavior, worked examples, or an
  independent reference, not the implementation under test. Test observable
  results; source-text matching does not prove runtime behavior.
- Ground review findings in a reachable input or execution path and a concrete
  impact, with source or test evidence. Respect the user's accepted tradeoffs.
- Implement originally. Review licensing/attribution before importing third-party
  code, tests, or fixtures; do not copy GNU implementation code into this MIT repo.
- Check the final diff, relevant commands and examples, and links. Use the exact
  [CI workflow](.github/workflows/pull-request-check.yml); report checks actually run and limitations.
  Do not weaken tests, hide errors, or fabricate benchmark evidence to obtain a pass.
- Reuse passing checks while their code, fixtures, configuration, and relevant
  environment remain unchanged. Rerun only checks affected by new changes,
  failures, or unresolved concerns; preparing a commit or PR adds no check run.
  Documentation-only changes use the documentation checks in the CI workflow.
- Documentation-only work establishes or audits contracts. Report implementation
  defects as concrete follow-ups unless a scoped fix is authorized.

## Skills and delivery

Use the relevant [repository skills](docs/agent-skills.md): `clis-explore` for
specifying a utility, `clis-audit` for standards assessment, `clis-cli-regressions`
for tests/fixtures, `clis-verify` for checks, and `clis-git-delivery` for Git/PR work.
An audit is not required for every small edit; tie its scope to the request.

Follow the [Git conventions](docs/agent-skills.md#git-conventions) for branch names,
commits, and PR titles. Use the [PR template](.github/pull_request_template.md),
reporting behavior/API impact, evidence, accepted trade-offs, and remaining gaps.
