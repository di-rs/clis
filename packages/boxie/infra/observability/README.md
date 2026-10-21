# Boxie monitoring

Use Grafana to inspect Boxie logs and metrics. Application JSON logs pass through
Docker and journald to Vector, which validates them and sends them to VictoriaLogs.
Prometheus collects metrics; Grafana queries both stores.

Terminal output is never collected. Request and sandbox IDs are searchable log
fields, not metric labels. Vector reads the host journal without a Docker socket.

## Install or maintain the stack

The [operations guide](operations.md) covers host journal permissions, private
networks, configuration, storage budgets, upgrades, and activation checks.
For the shared host, [setup](../../../../infra/environments/single-host/README.md)
composes these services with the playground. Standalone Boxie does not collect
playground logs unless the host adds its collector configuration.

Dashboards have no published origin ports. Configure
[operator access](../ingress/README.md) before exposing Grafana or Temporal UI.

## Find a failed request

Copy the response's `X-Request-ID`. In Grafana Explore, select VictoriaLogs and
search `request_id:="<UUID>"`. Use `sandbox_id` to follow creation, reconnects,
and later stop requests across separate request IDs.

See [request correlation](operations.md#request-correlation) for missing
historical IDs and [cross-service tracing](../../../../infra/environments/single-host/logging.md)
for the playground-to-Boxie path.

## Know the limits

Logging is best effort. Bounded queues drop diagnostics under pressure so logging
does not block provisioning or terminal I/O. Retention depends on storage budgets;
seven days of searchable history is not guaranteed.

Preserve Vector's buffer and checkpoint together during upgrades. Validate host
journal access, storage pressure, dashboard authorization, and recovery on the
actual Linux host before production use. Configuration tests cannot establish
those conditions.
