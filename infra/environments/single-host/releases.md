# Release and maintain the shared host

## Choose a release

Run either release manually from Actions on `master`:

- [🚀 cli-playground Release](../../../.github/workflows/cli-playground-release.yml)
  builds and deploys the cli-playground backend and frontend together in the application image.
- [🚀 Boxie and Sandbox Release](../../../.github/workflows/boxie-release.yml)
  builds the Boxie image and sandbox from one commit, then updates Boxie followed
  by its default sandbox image. It does not release the cli-playground application.

A failed stage stops the remaining deployments in its workflow; completed stages
are not rolled back automatically. Boxie stages use the `boxie` environment;
the cli-playground release uses `cli-playground`.
Each stage calls the shared [release transport](release.sh) for SSH, image settings
and temporary registry credentials; service scripts own migration and readiness checks.

## What a release changes

The Boxie stage pulls its image, stops API and supervisor, runs Alembic migration,
and starts both services with the metrics overlay. PostgreSQL and Temporal stay
up. Authenticated readiness must pass before the next stage. Pending requests may
resume after the restart; an empty capacity table is not a release condition.
The separate native host acceptance run covers terminal allocation and cleanup.

The sandbox stage changes Boxie's default profile digest and recreates
Boxie's supervisor, preserving other profile fields. The backend stage pulls
and recreates only `backend`; its proxy stays up.

## Queue and retry behavior

Setup and release workflows share one `sanctuary-production` queue. Each run selects
current `master` after entering the queue, then builds and deploys that checkout.
If a run fails, choose **Re-run all jobs** or start a new workflow run. Re-running
only failed jobs keeps the earlier source selection and is rejected before
deployment or provisioning starts.

Builds can run before production activation; deployments still require
`DEPLOYMENT_ACCEPTANCE_PASSED`. Pushes do not
trigger releases.

## Inspect a failed release

An unsuccessful SSH or Compose command fails the Actions run. On the host, inspect
services and recent logs before rerunning the workflow:

```sh
docker compose --env-file /etc/sanctuary/boxie.env --env-file /etc/sanctuary/boxie-image.env \
  -f /opt/sanctuary/boxie/compose.production.yml -f /opt/sanctuary/boxie/compose.operations.yml \
  -f /opt/sanctuary/boxie/compose.observability.yml ps
docker compose --env-file /etc/sanctuary/boxie.env --env-file /etc/sanctuary/boxie-image.env \
  -f /opt/sanctuary/boxie/compose.production.yml -f /opt/sanctuary/boxie/compose.operations.yml \
  -f /opt/sanctuary/boxie/compose.observability.yml logs --tail 100 api supervisor
docker compose --env-file /etc/sanctuary/cli-playground.env --env-file /etc/sanctuary/cli-playground-image.env \
  -f /opt/sanctuary/cli-playground/compose.production.yml ps
```

A migration failure leaves Boxie's API and supervisor stopped. Check migration
output and database state before retrying; do not start an older application
against a newer schema. After an SSH interruption, first check whether Compose
finished the operation and whether the application is healthy. Fix the cause and
rerun the workflow. There is no automatic rollback or separate deployment state
to clear.

## Acceptance and maintenance

Run local checks from the repository root:

```sh
uv sync --all-packages
uv run pytest infra/host/tests infra/environments/single-host/tests \
  packages/boxie/tests/test_deployment.py tools/cli-playground/tests/test_deployment.py \
  tools/cli-playground/tests/test_infra_workflows.py
```

On an authorized native Linux amd64 host, exercise fresh setup, an unchanged
rerun, both release workflows, migration failure and retry, metrics, tunnel
configuration and preview publication. The local command tests cannot establish
those host behaviors. The [host requirements](../../../packages/boxie/infra/host-requirements.md)
include the runsc and backup checks.

## Certificates and backups

The generated Boxie TLS leaf certificates last 90 days and the CAs last 365 days.
Plan renewal separately. Maintain an off-host backup and rehearse restoration
before relying on recovery; the protected disk alone is not a
backup.
