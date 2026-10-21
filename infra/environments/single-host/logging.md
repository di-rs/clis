# Trace requests across services

## Search across services

Copy an HTTP response's `X-Request-ID`, open Grafana Explore, select the VictoriaLogs
datasource and search `request_id:="<UUID>"`. Leave the service filter unset to see
cli-playground, Boxie API and supervisor events together. Use `sandbox_id` for
creation, later stops and reconnects that have different request IDs.

## Install the collector extension

Host setup installs the optional [collector overlay](observability/compose.yml)
after starting the service-owned projects. It adds Playground's own validator
from `/opt/sanctuary/cli-playground/observability/vector.yaml` to Boxie's collector.
Standalone Boxie keeps its original collector configuration and dashboard scope.
The extension has a separate bounded 512 MiB disk buffer, so reserve another
512 MiB on the collector volume; log-store retention is shared and unchanged.

## Upgrade request correlation

1. Apply Boxie Alembic revision `0006_request_correlation`, then update both Boxie
   processes. Its nullable columns allow older writers during rollout.
2. Apply the playground schema upgrades before starting its gateway.
3. Install the collector extension.

Existing rows retain an unknown request origin until a new
action supplies one, so historical operations can leave gaps in an ID search.

### Roll back an application upgrade

Keep Boxie's nullable correlation columns during application rollback. Playground
checks its exact schema revision: to return to the preceding PostgreSQL release,
stop the gateway and downgrade its database to `0001_metadata` before starting
that release. This removes diagnostic IDs while preserving pending operations.
The [database guide](../../../tools/cli-playground/infra/docs/database.md#database-and-cutover)
covers backup and the cutover from file-based state.

## Verify the deployment

Before activation, verify request and stop IDs through restart recovery on native
Linux amd64 with runsc, and verify journald collection after an outage. Local
Vector validation and Docker Desktop transport tests do not establish those host
gates. The [Boxie logging guide](../../../packages/boxie/infra/observability/README.md)
covers journal permissions, retention and pressure checks.
