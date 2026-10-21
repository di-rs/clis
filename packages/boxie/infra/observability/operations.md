# Operate Boxie monitoring

Boxie emits sanitized JSON on stdout. Docker delivers it through a nonblocking
4 MiB buffer to bounded persistent journald. Vector reads only the API and
supervisor container tags, validates the event allowlist, and sends JSON lines
to VictoriaLogs. Grafana queries VictoriaLogs and Prometheus. Request, sandbox,
workflow and run IDs are searchable fields; they never become stream labels or
metric labels. Sandbox PTY output is never collected. Vector has no Docker socket.

The [collector Dockerfile](collector/Dockerfile) copies the pinned upstream
Vector 0.59.0 binary onto pinned Ubuntu 26.04 with systemd/journalctl and its runtime
libraries. The official Vector Debian image bundles journalctl 257; Vector rejects
`current_boot_only: false` with versions 250–257, so that image cannot preserve
cross-boot collection. Ubuntu supplies journalctl 259. Native validation checks the
actual source under UID/GID 65532; a supplementary host journal group grants read
access. `versions.json` records the upstream image and Grafana plugin pins.

## Setup

Run these commands on the Linux Docker host, from the repository root. Create
private monitoring and dashboard bridges once; they cross independently owned
Compose projects:

```sh
docker network create --internal boxie-metrics
docker network create --internal boxie-grafana-origin
docker network create --internal boxie-temporal-ui-origin
```

Copy `observability.env.example` to a private environment file, set the Boxie
installation UUID, host ID and Temporal namespace to the actual application
configuration, and source/export it. Use a UUID for the installation and only
letters, numbers, underscores and hyphens for namespace/host values; these values
are inserted into Prometheus regular expressions. Get the numeric supplementary
journal group using `getent group systemd-journal`. Verify that this group can read
the persistent host journal. Install `journald.conf` as a systemd drop-in, enable
persistent storage, restart journald, and check `journalctl --disk-usage`.

Provision a private Grafana admin password file readable by UID 472. Install the
VictoriaLogs datasource plugin from the URL in `versions.json`, verify its SHA256,
and unpack it to `OBS_GRAFANA_PLUGIN_DIR`, preserving the archive executable bits
for its backend binaries. Grafana uses only this mounted plugin;
startup downloads and anonymous access are disabled.

Substitute the three nonsecret Prometheus values with the system `envsubst` tool.
Prometheus does not expand environment variables in YAML. The queue filter covers
the exact installation/host lifecycle and maintenance queues, including the
configured namespace:

```sh
set -a
. /private/boxie/monitoring.env
set +a
: "${BOXIE_INSTALLATION_ID:?}" "${BOXIE_HOST_ID:?}" "${BOXIE_TEMPORAL_NAMESPACE:?}"
envsubst '${BOXIE_INSTALLATION_ID} ${BOXIE_HOST_ID} ${BOXIE_TEMPORAL_NAMESPACE}' \
  < packages/boxie/infra/observability/prometheus.yml > "$OBS_PROMETHEUS_CONFIG_FILE"
docker compose --env-file /private/boxie/monitoring.env \
  -f packages/boxie/infra/observability/compose.yml build vector
docker compose --env-file /private/boxie/monitoring.env \
  -f packages/boxie/infra/observability/compose.yml up -d
```

The local collector defaults to `boxie-collector:local`. For a production release,
publish the built collector separately, set `OBS_COLLECTOR_IMAGE` to its immutable
registry digest, and start Compose with `--no-build` using that approved artifact.

Compose initializes the Vector and VictoriaLogs volume roots once with a
networkless, CHOWN-only service, then starts those nonroot services. Grafana and
Prometheus use the writable directories owned by their upstream images.
Compose owns four independent persistent volumes: journal checkpoint/buffer,
searchable logs, Prometheus and Grafana. Start Boxie with its base manifest plus
`packages/boxie/infra/compose.observability.yml`; start Temporal with its base manifest
plus `packages/boxie/infra/temporal/compose.observability.yml`. Base deployments need no
`OBS_*` values or monitoring bridges. Grafana and Temporal UI have no published
ports. Follow the [operator ingress guide](../ingress/README.md) for Access/Tunnel.

For an existing deployment, preserve its four current volumes and checkpoints.
Add a Compose override setting each matching volume key (`vector-data`, `logs-data`,
`prometheus-data`, `grafana-data`) to `external: true` and `name: <existing-name>`.
Inspect the merged `docker compose config` and confirm those exact names before
starting the replacement project. Keep the collector buffer and checkpoint together
in `vector-data`. Do not remove the previous volumes during cutover; switch to
Compose-owned storage only after an explicit data migration and verification.

## Resource and failure behavior

Grafana has a fixed hard 256 MiB memory cap. VictoriaLogs has a seven-day maximum
searchable age and a 256 MiB cleanup target. Its newest two daily partitions are
protected and may exceed the target; seven days of history is not guaranteed.
The journal has a 1 GiB persistent/128 MiB runtime budget, keeps 4 GiB free and has
a two-day age limit. Vector's disk buffer is 512 MiB, separate from the journal
and store; the final one-event memory stage drops under pressure. Vector reserves
a 128 MiB segment internally, so usable unread capacity is smaller than the
configured allocation. Prometheus has seven-day/2 GiB retention; reserve at least
3 GiB for its WAL and compaction. Monitor every backing filesystem, adding its
mount to node-exporter filters and disk alert selectors when storage is not on `/`.

Logging producers sanitize before a stdlib QueueHandler/QueueListener queue,
count drops and drain for at most two seconds at shutdown. Events over the byte
limit are dropped whole, preserving valid JSON and intact IDs. One daemon writes an
unbuffered duplicate of stdout; a blocked sink cannot hold a Python buffered-stream
lock during interpreter shutdown. Standard streams and their flags stay usable.
Raw `print`/fd writes are not telemetry APIs; application and dependency logging
must go through the configured handlers for sanitization. During collection loss,
Boxie continues provisioning and terminal work. Logs/counters are best effort.

Successful `/healthz` and `/readyz` requests emit no request logs. HTTPX uses a
WARNING threshold, so routine probe responses do not become dependency events.
Failed probes and ordinary application requests remain visible, and every request
still updates HTTP metrics. Empty command-recovery and cleanup passes emit no
per-run log events; the [host requirements](../host-requirements.md#native-maintenance) describes
their cadence and recovery behavior.

The supported `prometheus-client` HTTP server serves cached process/SQL gauges
on private unpublished ports. Its request threads are upstream-owned and bounded
by the process/container PID and memory limits, rather than a second custom HTTP
server. Bind failures retry without stopping Boxie; shutdown is process-owned.
SQL refresh uses a separate tiny pool with a bounded timeout. Stale gauges are
omitted and unknown cleanup ages remain unknown. Worker probes are retained:
Temporal server counters do not prove live pollers on all four owned queues.

## Validation and activation

The default Python suite checks privacy, context, bounded queues, HTTP/PTY
progress, stale SQL state, Compose isolation and ingress configuration. Existing
lifecycle CI also runs real PostgreSQL/Temporal telemetry checks, native Vector
privacy tests, Prometheus config/rule validation and a short Linux journal →
Vector → VictoriaLogs → Grafana datasource smoke.

Before production activation:

1. Confirm both dashboard hostnames through Access: unauthenticated requests are
   rejected, wrong-audience tokens fail, authorized requests succeed, and an
   unconfigured hostname gets the tunnel's final 404. Check there are no public
   origin, ingestion or metrics ports.
2. Trigger a failed request and search its request/sandbox IDs in Grafana. Check
   provisioning, capacity, cleanup, leases, pollers and telemetry health on the
   same timeline. Stop Vector and confirm real provisioning and terminal I/O
   continue. Restart it and confirm buffered diagnostics become searchable.
3. Exercise pressure on a disposable backing filesystem, including a stalled/full
   journal, buffer overflow, VictoriaLogs cleanup and Prometheus WAL headroom.
   Confirm drops/alerts, bounded shutdown and emergency `journalctl` access.
4. Check the collector's host journal permissions under UID 65532 and the supplied
   supplementary GID. Verify Grafana stays within 256 MiB during dashboard use.

Live Access, whole-host/storage pressure and power-loss recovery require the
actual Linux deployment. Ordinary tests do not establish those activation claims.

## Request correlation

For a failed authenticated request, copy its `X-Request-ID` response header and
search `request_id:="<UUID>"` in Grafana Explore's VictoriaLogs datasource. The
[consumer contract](../../docs/consumer-api.md#follow-a-request-through-logs) describes
accepted IDs and separate creation, stop and connection actions. Use `sandbox_id`
to inspect a sandbox across all of those actions.

Provisioning and cleanup result events restore the committed creation and stop
origins, even when one observation reports both results. Activity lookups use a
separate one-connection telemetry pool with a 100 ms deadline. When that lookup
fails, lifecycle work continues with resource/workflow fields and an unknown
origin. Old records retain unknown historical origins.

This collector remains Boxie-only by default. A host may add a consumer-owned
configuration with a second `--config` argument; the
[shared-host guide](../../../../infra/environments/single-host/logging.md#search-across-services)
shows the composed deployment. Its common-field search omits the Boxie service
filter. The Boxie dashboard retains its existing default scope.
