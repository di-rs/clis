# Infrastructure architecture

The shared-host environment composes Boxie and CLI playground on one Linux host.
Pulumi provisions the Hetzner server, disk, DNS, and operator access. Docker Compose
runs service-owned projects; GitHub Actions uses pinned SSH for setup and releases.

## Ownership and access

[Host provisioning](../host/) supplies the machine foundation.
[The environment composition](../environments/single-host/) connects the
[Boxie deployment](../../packages/boxie/docs/deployment.md) and
[playground deployment](../../tools/cli-playground/docs/deployment.md).
Each service owns its manifests, runtime configuration, and migrations.

The playground proxy is the public application entry point. Boxie's API, Docker,
PostgreSQL, and Temporal stay private. Cloudflare Access protects operator dashboard
routes. Application release credentials have root SSH authority on the shared host;
they do not isolate one application's deployment operators from another's.

## Lifecycles and state

Host setup initializes persistent dependencies and composes the applications.
Boxie and playground releases are separate, and PostgreSQL and Temporal upgrades
remain explicit maintenance operations. The shared production queue serializes
setup and releases; completed stages are not automatically rolled back on failure.

PostgreSQL, artifact storage, upload staging, and telemetry have distinct owners.
The protected host disk is not a backup. Service-specific recovery requirements
remain in their database and deployment guides; [setup](setup.md) and
[releases](releases.md) explain host-level coordination.

The host's [observability extension](observability.md) adds playground collection
to Boxie's monitoring deployment. Its configuration stays with the environment;
service-specific event formats and privacy rules remain with each service.
