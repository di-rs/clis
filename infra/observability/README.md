# Lifecycle telemetry

The API owns global SQL gauges. Its dedicated one-connection pool reads one
MVCC aggregate statement every 5 seconds (2-second attempt budget, 0.5-second
acquisition and 1-second statement timeout). Scrapes read an immutable cache,
never SQL or agent records. After 30 seconds without a successful refresh,
SQL gauges disappear; snapshot health and age remain. Unknown cleanup ages
are counted and omit the oldest-age gauge. Zero means a successful known-empty
sample. Agent lease samples use current-process monotonic deadlines and expire
after 5 seconds; known-empty has an active count of zero and no lease minimum.

Dependency evidence expires after 30 seconds. Independent probes run every
15 seconds with a shared 5-second budget; SQL readiness follows the dedicated
snapshot attempt. Agent execution and lease caches refresh every second.
Temporal worker presence uses each queue's last poller access with the existing
90-second allowance; the alert adds its 2-minute hold. Idle traffic rates are
not worker-presence signals.

Only acknowledged SQL transitions emit accepted, requested-stop, ready,
provisioning-completed and cleanup-completed events. Repeated admission,
observations, final confirmations and workflow replay emit no additional durable
outcomes. Counters cover observed transitions in the process lifetime and may
lose the commit-to-diagnostic crash window. They are not a historical ledger and
are not reconstructed on restart. Gauges do rebuild from retained SQL facts.

`boxie_provisioning_ready_observation_duration_seconds` measures admission to the
first SQL receipt of durable ready evidence, including scheduling/reporting
latency. Failed/stopped outcomes have no duration sample and become final only
at confirmed removal. Ready evidence arriving before that finalization wins;
coalesced final observations carrying readiness still count one success. Later
reports cannot reclassify an already cleaned row. Activity/cleanup failures and
reconciliation are attempt events. Terminal frames are never events.

Each process has an isolated registry. Fixed method, route-template, status,
state, outcome, dependency and outbox-kind catalogs bound application dimensions.
IDs remain validated searchable log fields. Queue occupancy, drops and persistent
sink availability come from the logging runtime's immutable statistics.

## Listeners and deployment handoff

Python listeners bind loopback by default; set `BOXIE_METRICS_HOST=0.0.0.0` or
`PLAYGROUND_METRICS_HOST=0.0.0.0` explicitly inside the private container network.
The exporter is never mounted on an application router. Python bind failures
retry every 15 seconds without blocking lifecycle startup or flooding logs.
The synchronous process owner starts log drain and listener shutdown under one
shared monotonic 2-second deadline after the event loop exits. Both joins use
only remaining time; repeated closes cannot renew the deadline. The
Temporal SDK Runtime exports on its own listener; construction failure records a
configuration error and falls back to safe Core log forwarding until process
restart (the pinned SDK has no public Runtime close).

| Job | Private target |
| --- | --- |
| boxie-api | boxie-api:9101 |
| boxie-worker | boxie-worker:9102 |
| boxie-agent | boxie-agent:9103 |
| playground | playground:9104 |
| boxie-api-sdk | boxie-api:9201 |
| boxie-worker-sdk | boxie-worker:9202 |
| temporal | temporal:9090 |
| vector | vector:9598 |
| victorialogs | victorialogs:9428 |
| node-exporter | node-exporter:9100 |
| prometheus | prometheus:9090 |

Task 3 owns deployment wiring: a dedicated private metrics bridge, separate
query and ingest networks, no host-published metrics or ingress routes, fixed
aliases above, native server/exporter configuration and final allowlist validation.
The supplied host rules select `/`; deployments must explicitly update that
finite mount list if approved storage has another backing filesystem.

Native Vector buffer names and units were verified against the selected 0.59.0
executable; the sink ID is `storage`. The lazy buffer-discard counter includes
intentional `drop_newest` loss. `increase` can miss the first-ever loss before the
first observed counter sample; absence is not proof of zero drops. Display the
native lifetime counter alongside the rate. Missing/zero maximum buffer capacity
is unhealthy; absent occupancy alone may be an empty startup buffer.

VictoriaLogs families were verified against 1.53.0 with metadata enabled. Native
SDK latency families were scraped from the pinned Python SDK 1.34.0 on macOS
arm64; they use seconds and `_total` counter suffixes. The SDK allowlist preserves
fixed activity/workflow types to avoid collapsing distinct series. Temporal
server and host exporter families follow the preserved pinned-source contracts;
final Linux executable, topology, cardinality and resource/fault validation
remain deployment gates. Local substituted Docker tests do not establish gVisor
or production activation readiness.

## Rules

Scrape/evaluation cadence is 15 seconds with a 5-second scrape timeout. Rules
cover cleanup older than 120 seconds for 2 minutes, full capacity for 5 minutes,
missing required targets/worker evidence for 2 minutes, disk available below
20% for 5 minutes or below 10% for 2 minutes, collector buffer above 80% for
5 minutes, and increasing drops for 2 minutes. Freshness, unknown cleanup age,
log-sink availability and disk/buffer metric health are separate conditions.
Rules are visible in Prometheus without external notification configuration.

Use the pinned image for all three checks:

```sh
docker run --rm --network none --entrypoint /bin/promtool \
  -v "$PWD/infra/observability:/etc/prometheus:ro" \
  docker.io/prom/prometheus@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e \
  check config /etc/prometheus/prometheus.yml
```

Repeat with `check rules /etc/prometheus/rules/boxie.yml` and
`test rules /etc/prometheus/tests/fixtures/rules.yml`.
