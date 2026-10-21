# Shared-host deployment

This deployment puts Boxie and the browser playground on one Hetzner server.
Pulumi creates the server, disk, DNS, and operator access. Docker Compose runs the
services; GitHub Actions performs setup and releases over pinned SSH.

## Choose an operation

| Task | Guide |
| --- | --- |
| Provision a host and hand credentials to release workflows | [Set up the host](docs/setup.md) |
| Release services, inspect a failure, or plan maintenance | [Releases and recovery](docs/releases.md) |
| Follow a request through playground and Boxie logs | [Request tracing](docs/observability.md) |

Setup can create paid cloud resources. It needs an existing Pulumi state bucket,
provider credentials, and the GitHub environments described in the setup guide.
Keep state credentials and backups outside the VM.

## What a release changes

Boxie and the playground have separate manual release workflows. Boxie's release
updates its service and sandbox image. The playground release updates its backend
and frontend together. PostgreSQL and Temporal upgrades are separate operations.

Setup and releases share a production queue and select current `master` after
entering it. Deployments require `DEPLOYMENT_ACCEPTANCE_PASSED`. A failed stage
stops the workflow, but completed stages are not automatically rolled back.
After a failure, inspect the host before starting a new run or rerunning all jobs.

## Before production use

Verify DNS, TLS, operator access, native gVisor execution, host capacity, and an
off-host backup restore. The protected disk is not a backup. Application release
credentials grant root SSH authority on the shared host, so the two applications
are not isolated from each other's deployment operators.

See [host requirements](../packages/boxie/docs/deployment.md#deployment-prerequisites) for
acceptance checks and [architecture](../docs/architecture.md) for the service layout.

Read [infrastructure architecture](docs/architecture.md) for ownership and service boundaries.
