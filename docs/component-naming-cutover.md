# Reset the disposable installation

This release renames the components and makes Boxie own one default sandbox
environment. The public creation API now accepts only an artifact ID. Previous
application pending-operation files and consumer profile settings are incompatible.
The existing installation is disposable: replace it before deploying this release.
No backup or retained-state conversion is part of this reset.

## Prepare GitHub

Merge the reviewed release, then follow the environment and secret inventory in
[shared-host setup](../infra/environments/single-host/README.md#prepare-the-sanctuary-environment).
Use environments `sanctuary`, `boxie`, `cli-playground` and
`cli-playground-preview`. Set repository variable `DEPLOYMENT_ACCEPTANCE_PASSED`
to `false` and leave `SANCTUARY_INFRA_DRIFT_CHECK` disabled during setup. Pause PR
preview publication while the host is replaced.

The published images are `sanctuary-boxie`, `sanctuary-boxie-sandbox`,
`sanctuary-cli-playground` and `sanctuary-collector`. Grant repository Actions and
deployment pull access to the packages. The application image includes both its
Frontend and Backend. Only the Boxie release updates the sandbox image.

## Replace the old stack's resources

Skip this section if no infrastructure has been provisioned. Otherwise, run
[Reset Sanctuary infrastructure](../.github/workflows/sanctuary-reset.yml) from
GitHub Actions:

1. Open **Actions → Reset Sanctuary infrastructure → Run workflow**.
2. Select `master` and enter `destroy-production` in the confirmation field.
3. Run the workflow and approve the `sanctuary` environment if requested.
4. Wait for the reset to finish successfully before running setup.

The workflow uses the existing backend and `production` stack configured in
`sanctuary`. It disables Pulumi and Hetzner deletion protection, then permanently
deletes the server, data disk and the stack's other resources. It keeps the empty
stack and R2 backend. No local Pulumi or SSH commands are needed for the reset.
In GitHub's `SINGLE_HOST_CONFIG`, use `"protect": true` for the replacement
installation.

If reset fails, leave releases and previews disabled, inspect the failed step and
rerun after correcting the cause. A failed reset can leave resources unprotected
or partially deleted. Do not run setup until reset succeeds.

## Install and verify

1. Run **Single-host Sanctuary setup** from `master`. It builds the images,
   provisions the replacement host and initializes fresh databases and credentials.
2. Run the shared-host guide's
   [SSH credential handoff](../infra/environments/single-host/README.md#give-application-workflows-their-ssh-credentials).
   The replacement has new host keys and credentials. Its preview token is now
   `/etc/sanctuary/cli-playground/preview-token`.
3. Check HTTPS, operator Access, authenticated Boxie readiness and monitoring.
   Run [Boxie lifecycle integration](../.github/workflows/boxie-lifecycle.yml)
   and the [shared-system lifecycle check](../.github/workflows/sanctuary-lifecycle.yml)
   manually for native Linux/runsc coverage. Then verify a preview on the actual host.
4. Re-enable preview publication and set `DEPLOYMENT_ACCEPTANCE_PASSED=true`
   after acceptance for this disposable installation. Remove unused old GitHub
   environment names and obsolete workflow/check references.
5. Merge the separate cleanup PR only after reset and verification. It removes
   the one-time naming converters and this procedure. Database revision history
   stays because new installations still apply it.

The earlier offline naming converters remain temporarily for the staged cleanup;
they are not an upgrade path to this new API. Do not run them and then reuse old
application state with the fixed-default deployment.
