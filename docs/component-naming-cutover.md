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

Skip this section if no infrastructure has been provisioned. Otherwise, use a
checkout of the reviewed revision and the existing Pulumi backend and production
stack. Load `PULUMI_BACKEND_URL`, `PULUMI_CONFIG_PASSPHRASE`, `AWS_ACCESS_KEY_ID`
(the R2 access key), `AWS_SECRET_ACCESS_KEY` (the R2 secret), `HCLOUD_TOKEN` and
`CLOUDFLARE_API_TOKEN` from your secret manager without printing them.

Run from the repository root:

```sh
uv sync --locked --all-packages
pulumi login "$PULUMI_BACKEND_URL"
pulumi -C infra/environments/single-host stack select production
pulumi -C infra/environments/single-host config refresh --force
pulumi -C infra/environments/single-host config set sanctuary:protect false
pulumi -C infra/environments/single-host up --refresh --diff --suppress-outputs
pulumi -C infra/environments/single-host destroy --refresh
pulumi -C infra/environments/single-host config set sanctuary:protect true
```

Review each preview and approve only the intended stack's operations. The update
disables both Pulumi and Hetzner deletion protection; destruction removes the
server, data disk and the stack's other resources. It permanently deletes
application data. Keep the empty stack and R2 backend for setup. In GitHub's
`SINGLE_HOST_CONFIG`, use `"protect": true` for the replacement installation.

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
