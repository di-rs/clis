# Focused CLI setup implementation plan

Approved in conversation on 2026-10-05, including deriving the application name
from Clap metadata. This plan replaces the broader subscriber configuration design;
the [observability contract](../../observability.md) owns current requirements.

Base revision: `cb4f703`. Continue the existing PR; do not migrate other utilities
or modify Kara incidentally. Keep handoff and scratch files out of commits.

## Interface and scope

- Flatten `cli_tracing::LogArgs` and call `run::<Cli>(&cli.logging, operation)`.
- `Cli: clap::CommandFactory` supplies the command name, including custom names.
- One setting: flag > `CLIS_LOG_LEVEL` > off. Text stderr, no ANSI, no short aliases.
- Install a global subscriber and log bridge once before the operation. Use upstream
  formatting, debug stage spans, and close records; no separate timing switch.
- Keep domain APIs typed and independent of setup. Biggie retains
  `gen_random_lines(impl Write, u64) -> io::Result<()>`.
- Return expected `ExitCode` outcomes unchanged. Unhandled anyhow/setup/sink failures
  return 1; other utilities' GNU diagnostic/status semantics need reviewed adapters.
- Keep a private checked writer so formatter callback errors cannot become success.

## Tasks and acceptance

1. Establish the affected baseline and write process regressions for debug timing,
   sole shared flag, precedence, name derivation, errors, and the log bridge.
   Observe failures on the old implementation before replacing it.
2. Replace public configuration/dispatch APIs with the shared arguments and entry.
   Preserve checked writes; test all levels, workers, expected nonzero outcomes,
   duplicate installation, disabled fields, cause chains and broken stderr.
3. Migrate Biggie's adapter, use log messages and debug stage instrumentation.
   Preserve direct API and partial-write tests, default output and data flushing.
4. Reconcile current guides and examples. Remove obsolete configuration recipes;
   document actual behavior and migration limits without duplicating the standard.
5. Run package then workspace checks in [CONTRIBUTING](../../../CONTRIBUTING.md#checks),
   rustdoc/example checks, links and diff review. Measure correctness-checked stage
   overhead and before/after startup, with raw samples and limitations.
6. Obtain a fresh review, resolve actionable defects, commit and update the existing
   PR. Check CI and leave the PR unmerged.

## Review focus

- Log records must reach the subscriber, including from joined worker threads.
- Required errors must appear once with the Clap name even with logging off.
- An expected nonzero outcome must retain its status without becoming an error report.
- Setup failure must prevent the operation; output failures must not become success.
- No removed configuration remains presented as current, and domain APIs do not
  acquire process setup or Clap dependencies in their implementation.
- Distinguish disabled instrumentation, stage collection, and startup measurements.

## Evidence

The clean baseline passed 36 affected tests. Three new Biggie process regressions
failed as expected before implementation. Final checks and measurement limitations
are recorded in the PR and [overhead record](../../../utils/cli-tracing/overhead.md).
