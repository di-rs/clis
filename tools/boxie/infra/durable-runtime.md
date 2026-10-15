# Durable runtime

Boxie requires the independently operated [PostgreSQL](../../../infra/platform/README.md)
and production [Temporal](../../../infra/temporal/README.md) packages on the private
control network. Run their explicit schema/namespace setup first. Production never
starts a Temporal development or test server. All certificate holders are trusted
cluster administrators; the namespace is organizational, not an RBAC boundary.

Prepare a mode-0600 Boxie database DSN file (a PostgreSQL URI or libpq DSN). The
Boxie, Temporal persistence and visibility credentials must remain distinct.
Apply Boxie migrations explicitly from the installed Boxie environment:

```sh
uv run --locked python -m boxie.persistence.migrate --dsn-file /etc/boxie/postgres-dsn.secret
```

Generate `api.json`, `worker.json` and `agent.json` with `boxie_infra.config` as in
[the infrastructure guide](README.md). Provide the additional paths in
[config.example.env](config.example.env). API and worker mount separate Temporal
client identities signed by the dedicated client CA, verify the server's CA and
hostname, and receive no Docker socket or artifact directory. The agent receives
the Boxie DSN and agent credential, but no Temporal certificates.

The API commits admission, capacity and artifact references with a start outbox
record. Only the agent stores artifact bytes; PostgreSQL is the artifact metadata
authority. The worker dispatches durable notifications and executes lifecycle
activities. The agent revalidates the same SQL admission under the final-start row
guard. Stop is durable before cleanup starts. There is no direct-mode startup or
fallback. Do not reuse an old direct-mode data directory; migration and restart
terminal continuity are excluded.

`/readyz` reports orchestration readiness: SQL/outbox age and backlog, authenticated
agent execution/Docker readiness, Temporal health/namespace and recent workflow
and activity pollers. A command older than 30 seconds or backlog of 100 rejects new
admission. Matching retries, status and stop still use SQL when dependencies are
unhealthy. Stop requires the normal API DELETE, never Temporal termination.
Workers can restart without closing agent-owned terminals. Cleanup failures retain
capacity and artifact references and retry durably every 30 seconds after each
bounded cycle. Only fenced physical absence can release accounting.

The agent publishes authenticated `PUT /internal/observations` with its agent
credential. Consumer credentials cannot publish facts. Input reports coalesce for
10 seconds; attachment, detachment, exit and deletion trigger immediate delivery.
One latest fact per sandbox survives an API outage in agent memory; 30-second
workflow inspection repairs lost reports and renews the control lease. Sensitive
profile/command/environment values are resolved only inside activities; no PTY
bytes enter SQL observations or Temporal history.

## Integration test server

Time-skipping tests use the separate pinned test server 1.40.0. Set
`BOXIE_TEST_SERVER` to the platform-specific executable described in
[test-server-pins.json](../tests/test-server-pins.json); tests verify its SHA-256
and fail when it is missing or different. Download the versioned archive, verify
its archive hash, extract the listed member and verify its executable hash.
This test-only binary is never a production input. Run from the repository root:

```sh
UV_CACHE_DIR=/tmp/boxie-uv-cache uv run --locked pytest -m integration \
  tools/boxie/tests/test_temporal_lifecycle.py tools/boxie/tests/test_outbox.py
```

Time-skipping tests use deterministic activities and export/replay real histories.
The separate production-server tests exercise PostgreSQL, mTLS, HTTP, agent fencing
and resource accounting without skipping PostgreSQL or agent clocks. Native
Linux/runsc and full process-crash proof are additional integration gates.
