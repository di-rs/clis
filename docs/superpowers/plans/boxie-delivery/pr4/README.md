# Phase 4 delivery record

This change implements production composition and application release hardening
on top of phase 3 (`affa3906c68dc9d3ac4803bdf7b33cf726570831`). It opens the fourth
stacked PR; it does not merge the stack or activate a production environment.

## Review and verification

The independent whole-change review found five material issues: missing journal
installation, missing persistent-disk/product telemetry wiring, missing Boxie
checks in Playground readiness, tunnel credentials in command arguments, and an
output cap applied only after child exit. Each was corrected with a regression
that first failed and then passed. Journal/environment tests render actual owner
configuration; health checks use real HTTP peers including slow-drip responses;
setup tests execute the workflow shell against a recording Pulumi boundary; the
output test emits more than one MiB and proves termination before its deadline.

Root Ruff, formatting and strict mypy passed after those changes. The final
explicit integration run passed **11 tests in 37.84 seconds**: real Compose
activation/rollback with a random persistent sentinel and an untouched unrelated
component, plus independent service wheel/sdist installation and eight resolved
Compose topology cases. These tests require real prerequisites and fail when
selected without them. The frontend build and workflow/shell checks passed.
The final root pytest result is recorded in the PR description and commit hooks.

Live provider-backed Pulumi preview, first-host/reboot/disk failure verification,
production PostgreSQL/Temporal setup, native gVisor flow, public DNS/ACME/Access,
and combined-stack capacity remain activation gates in the
[single-host runbook](../../../../../infra/environments/single-host/README.md).
No production credentials or cloud environment were used for this implementation.

## Decisions and costs

- Branch from phase 3 rather than master to preserve the requested PR stack.
  Changing this choice requires a rebase.
- Require the full compatibility token map and service set to match the active
  release for ordinary updates. This supports conservative same-schema rollback;
  future schema/dependency transitions require an explicit operator procedure.
- Provision registry pull access separately using read-only credentials. The
  host does not receive the CI publication token; initial access is an operator
  setup requirement when the registry image is private.
- Treat cloud, DNS/Access, reboot, native platform and capacity checks as
  production activation gates. Local tests establish their narrower contracts;
  the draft PR cannot certify a selected VM for production.

- Keep the supported `./` reusable-workflow syntax while actionlint 1.7.12 lacks
  support for the newer `$/` form. Two inline exceptions silence only zizmor's
  syntax recommendation. Both forms resolve the caller commit according to
  [GitHub's workflow reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_iduses).
  Revisit these exceptions when the pinned actionlint supports the new form.

## Deferred minor

A custom `deploy --timeout 30` passes a zero remote budget and fails validation.
The ordinary 900-second default works. Use that default pending a follow-up
minimum-bound correction; this does not affect the shipped workflows.
