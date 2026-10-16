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

## Independent bounded storage

`observability-infra` is a declared root-uv workspace package. Its frozen
`ObservabilityConfig` and `Budgets` validate explicit inputs before
`render_config(config, destination: Path) -> tuple[Path, ...]` creates a **new**
configuration directory. Rendering never starts containers, creates networks or
volumes, installs journald settings, or reads process environment variables.
Pass `assets_dir` explicitly, pointing to this matching asset directory, including
when using an independently installed wheel. Missing assets fail before writes.
The returned paths are `compose.yml`, `vector.yaml`, `journald.conf`,
`observability.env`, `prometheus.yml`, and `rules/boxie.yml`.

`config.endpoints` is immutable `PrivateEndpoints`: `logs`, `log_ingest`,
`prometheus`, `grafana`, and `scrapes` (a tuple of `ScrapeEndpoint(job, address)`).
The private origins are `http://victorialogs:9428`, `http://prometheus:9090`, and
`http://grafana:3000`. `consumer_scrapes` defaults to `()`; a product composition
may supply `ScrapeEndpoint("playground", "playground:9104")`. Required-target
alerts follow exactly the selected jobs. No product package is imported.

Platform owns the shared internal metrics network and separate internal
Grafana/Temporal UI origin networks. Observability owns internal `ingest` and
`query` networks. Prometheus joins metrics/query, Vector metrics/ingest,
VictoriaLogs ingest/query/metrics, and Grafana query/Grafana-origin. A later
platform ingress tunnel joins only the two origins and its own egress network.
No telemetry service publishes a host port or joins the database/control network.
The metrics bridge is trusted peer connectivity, **not per-port authorization**;
listeners bound to all container interfaces remain reachable on other networks
that their service already owns. Temporal UI's explicit origin alias is
`temporal-ui`. Application releases consume the platform metrics handle but do
not reconcile storage.

All long-lived infrastructure/application containers use journald with fixed
literal tags, nonblocking delivery and a 4 MiB Docker buffer. Operational Temporal
jobs also use bounded local journald; sandbox logging remains disabled. Only
`boxie-api`, `boxie-worker`, `boxie-agent`, and `cli-playground-gateway` enter
searchable storage. Native service text stays in the bounded local journal;
native metrics provide its searchable operational signals. Driver options never
include environment variables, labels, regular expressions, or container-name
expansion.

The source ID `journal`, sink ID `storage`, Vector data directory and external
volume identity must survive releases. Vector runs as `65532:65532` with the
explicit numeric host journal supplementary GID. Bootstrap must grant only read
and traversal permissions to the persistent journal and read access to machine
identity; it must prepare the Vector volume for that UID. The reader mounts only
that journal directory and machine identity read-only, plus its own writable
state. It has no Docker socket, host root, privileged mode or capabilities.
`current_boot_only: false` and explicit legacy `log_namespace: false` preserve
all-boots input and the selected source's `.message`/`CONTAINER_TAG` envelope.
Runtime-only fallback journals are omitted from searchable ingestion but remain
bounded and locally inspectable.

The renderer compiles the owned v1 schema into VRL checks: object shape, required
fields, field allowlist, types, ranges, enums, UUIDs, UTC timestamp, tag/service/role
agreement, and length bounds. Trusted environment/host are added only afterwards.
Malformed records produce a fresh small rejection event without any raw input,
parse error, journal metadata or environment enrichment. Only
`service,role,environment,host` are stream fields; approved IDs are searchable
fields. Input is capped at 8192 bytes. Each enriched serialized record is at most
16383 bytes plus its newline; batches contain at most 64 events, so their JSONL
wire body cannot exceed one MiB. The pre-serialization `batch.max_bytes` threshold
alone does not establish this bound. No compression, debug ingestion, raw rejected
body logging, or end-to-end acknowledgements are enabled. Collector crashes can
lose the checkpoint/durability window or duplicate recovery records; this is
best-effort diagnostics, not exactly-once delivery.

| Independent layer | Initial bound/target |
| --- | --- |
| Persistent journal | 1 GiB use; keep 4 GiB free; 2 days; rotate hourly |
| Runtime journal | 128 MiB |
| Vector disk buffer | 536870912 bytes; `drop_newest` |
| Vector HTTP batches | at most 1 MiB and 64 events; flush after 1 second |
| VictoriaLogs | 7 days, 10 GiB deletion target; `/victoria-logs-data` |
| Prometheus | 7 days, 2 GB (2147483648 bytes in its binary units) retention; allocate at least 3 GiB for WAL/compaction |
| Memory caps | Vector 256 MiB; logs 1 GiB; Prometheus 512 MiB; Grafana 256 MiB; node exporter 64 MiB; later tunnel 128 MiB |

The four external volumes are distinct: Vector state/buffer, VictoriaLogs,
Prometheus, and Grafana. The host/bootstrap owner allocates them and sets ownership
(Vector 65532:65532, logs/Prometheus 65534:65534, Grafana 472:0). Limits are tunable
starting settings, **not filesystem quotas or measured capacity**. Seven days is
a retention objective subject to size-based deletion, not a minimum guaranteed
history window. PR4 must measure combined headroom with PostgreSQL, Temporal and
eight sandboxes. Journal installation/reload belongs to host bootstrap, not this
renderer. Application logging must remain independent of collector availability.

`backing_mounts` is the finite actual host filesystem mountpoint set, default
`("/",)`. Bootstrap must resolve all approved persistent data paths (including
external volumes) with host `findmnt --target`; directories are not necessarily
mountpoints. The renderer supplies an anchored escaped exporter include regex,
matching scrape filtering and an absence check for **every** expected mount.
The warning/critical thresholds stay below 20% for 5 minutes / 10% for 2 minutes.
Node exporter enables only filesystem/meminfo, runs as 65534:65534 and has a
read-only recursive host-root bind with rslave propagation. `/host/run` and
`/host/var/run` are masked; `exporter_socket_masks` must additionally name actual
custom daemon socket directories. Bootstrap must verify recursive read-only
submounts on the target kernel, mask effectiveness and procfs/statfs permissions.
No host PID/network namespace or Docker group is required. Native Linux validation
must compare its finite mount series against host `df`; local Docker Desktop
results do not prove the production host topology.

## Frozen artifacts and activation inputs

[versions.json](versions.json) records the selected immutable official image
indexes, static Vector archive checksums, Ubuntu snapshot/package checksums, local
collector build identity and datasource plugin identity. The official Vector
Debian image is used **only** as the pinned public CA-bundle source: its systemd
257 cannot support the selected all-boots reader. The derived
[collector Dockerfile](collector/Dockerfile) uses Ubuntu's own systemd
259.5-0ubuntu3.4, snapshot `20261015T000000Z`, signed repository metadata, checks
all six added/upgraded packages and static archive bytes, and installs no live
mirror bootstrap metadata. Build with:

```sh
docker build --platform linux/amd64 -f infra/observability/collector/Dockerfile \
  -t YOUR_LOCAL_COLLECTOR infra/observability/collector
```

The arm64 inputs support local verification only. A local image/OCI ID is not a
published registry digest. Deployment requires a separately built and published
amd64 collector reference, supplied as `collector_image`; mutable tags and the
incompatible official collector are rejected. Publication/activation is separate
operator work and is never performed by the renderer. Native amd64 executable,
real binary-journal permission/cursor/restart/fault, and capacity checks remain
required before production claims. Synthetic envelope tests do not prove binary
journal ingestion or cross-boot/power-loss recovery.

Grafana provisioning belongs to the following task. Its owner supplies
`OBS_GRAFANA_PROVISIONING_DIR`, `OBS_GRAFANA_PLUGIN_DIR`, and
`OBS_GRAFANA_ADMIN_PASSWORD_FILE`. The plugin directory must be materialized from
the exact VictoriaLogs 0.32.0 ZIP in `versions.json`, SHA-256 verified before
extraction and mounted read-only. Prometheus 13.2.3 is bundled in the pinned Grafana
13.2.4 image. Keep normal plugin signature enforcement enabled; no unsigned-plugin
exception or automatic preinstall/update is configured. Supply an owned mode-0400
admin password file readable by UID 472 and a writable owned data volume. Do not
put credentials in rendered env files. Final login/datasource health, Access,
origin protection and provisioning tests belong to the Grafana/ingress owner.

[observability.env.example](observability.env.example) lists the caller seams;
[the fixture](tests/fixtures/observability.env) is nonsecret and deliberately uses
an unusable synthetic collector digest. It is for `docker compose config` only,
never activation. Service integration fixtures explicitly override journald to
bounded Docker local logging where they test SQL/Temporal/service packaging;
those checks are not journal delivery evidence.

Run the default configuration suite and explicit selected-executable checks:

```sh
uv run --locked pytest infra/observability/tests/test_configuration.py
OBS_TEST_COLLECTOR_IMAGE=YOUR_LOCAL_IMAGE_ID uv run --locked pytest \
  infra/observability/tests/test_configuration.py -m integration
```

The integration checks require Docker and the pinned images. They exercise VRL
sentinels and both target profiles through the pinned promtool, preserving the
reviewed rule scenarios and adding separate backing-mount failure cases. The
final smoke must additionally exercise real owned binary journals, persistent
cursor/restart, accepted-buffer recovery, full-buffer dropping, memory/disk
pressure and diagnostics sentinel checks. No blanket native acceptance follows
from `vector validate --no-environment` or Compose parsing.

Configuration references: [Vector HTTP buffering](https://vector.dev/docs/reference/configuration/sinks/http/),
[Docker nonblocking delivery](https://docs.docker.com/engine/logging/configure/#configure-the-delivery-mode-of-log-messages-from-container-to-log-driver),
and [Prometheus retention](https://prometheus.io/docs/prometheus/latest/storage/).

Platform bootstrap must explicitly create/validate the three shared bridge
handles before consumers start: Compose can omit declarations unused by platform
services, so PostgreSQL's `compose up` alone is insufficient. PostgreSQL stays off
these bridges. PR4 owns production network ownership/internal-property validation.

Native Temporal retention preserves `partition`, `forwarded`, and `task_priority`:
the selected executable emits distinct series that would collide if these were
removed. Initial accepted partitions are 0–3 and `__sticky__`, forwarding is
true/false/absent and priority is 3/absent/empty. Per-family operation, state and
source filters plus fixed unversioned worker/OTel scope filters precede label
removal. Change these bounds explicitly when configuring partitions, versions or
priorities; aggregate deliberately in dashboard PromQL after ingestion. The
retained native idle fixture preserves all 109 observed queue samples with unique
Prometheus identities. `no_poller_tasks` remains source-backed and was not emitted
by the healthy-work/idle probe.

Node exporter retains device/fstype identities only for the finite selected
mountpoints, with filesystem/meminfo collector filtering and no arbitrary
`device_error` text. Missing bytes for any expected mount trigger unknown-health
alerts. Device/fstype are trusted host dimensions, not guessed production device
names; actual Linux mount visibility and backing mount correctness remain a
bootstrap gate. `tests/fixtures/*-native.json` preserves the independent selected
exporter samples and hashes of the raw arm64 captures. Fixture `/etc/hosts` statfs
proves label semantics only; it is not production host-root validation.

The unobserved `no_poller_tasks` family uses the partition manager's base scope,
so its source-backed filter permits an absent worker version only for that
family. Other sampled queue families require `__unversioned__`. This distinction
comes from the pinned [partition emission site](https://github.com/temporalio/temporal/blob/v1.32.0/service/matching/task_queue_partition_manager.go)
and [partition scope construction](https://github.com/temporalio/temporal/blob/v1.32.0/service/matching/matching_engine.go),
not an observed failure-counter sample.
