# Trace playground requests

## Diagnose a failed request

Copy the `X-Request-ID` response header from the failed HTTP request and search
`request_id:="<UUID>"` in Grafana Explore's VictoriaLogs datasource. The public
boundary always generates this ID, forwards it to Boxie and the supervisor, and
includes it in bounded JSON service logs. Internal URLs, credentials, terminal
content and exception text are omitted. Logging drops diagnostics under pressure
without blocking requests.

WebSocket connections also have one server-generated ID, forwarded on the
internal handshake. Browser JavaScript cannot read handshake headers. A reconnect
starts a new action; `sandbox_id` still identifies the same terminal resource.

Saved admission, upload, stop and retirement operations retain their first
request IDs in PostgreSQL so later retries can be traced to the action that
started them. These optional diagnostic fields leave existing rows with an
unknown origin. Apply the [schema upgrade](database.md#schema-changes)
before starting the gateway. The
[host guide](../../../infra/docs/observability.md#search-across-services)
covers the optional collector extension and rollout order.
