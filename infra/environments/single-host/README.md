# Single-host environment

This is the initial Pulumi composition root, with one environment per stack. It
selects the shared host/storage platform, Boxie, optional Playground DNS, and
optional operator Access/Tunnel configuration. Components declare infrastructure
and nonsecret configuration; application activation belongs to the separate
release commands. No Pulumi callback starts containers or writes local artifacts.

The cloud resources are protected by default. The Hetzner host runs Ubuntu 24.04
on x86_64 and uses gVisor systrap. A separately attached ext4 volume backs
`/srv/boxie`, Docker’s entire data root and all shared data volumes. A bounded
first-boot wait allows the attachment to complete; absent storage aborts setup.
A persistent mount unit and Docker prestart guard keep reboot fail-closed too.

## Configure and provision

From the repository root, run `uv sync --locked --all-packages`. Keep the existing
remote Pulumi state backend; it is separate from application backups. In this
directory, initialize/select the intended stack and copy `Pulumi.example.yaml`
to `Pulumi.STACK.yaml`. Replace the installation UUID, approved registry digest,
SSH allowlist, and deployment environment secret reference. Supply `HCLOUD_TOKEN`
to Pulumi from the provisioning secret store, then run `pulumi preview` before
`pulumi up`. No cloud mutation is part of the repository’s default tests.

The Boxie image must use an immutable registry SHA-256 digest. A generic environment
requires no Playground hostname, product token or Cloudflare configuration. Add
the `playground` object to provision the product and wildcard DNS records and open
host ports 80/443. Its service receives Boxie’s private endpoint and consumer
credential reference through the environment. Product DNS/ACME tokens belong to
its consuming setup/release processes and never become host bootstrap inputs.

To select operator ingress, add `operators`, supply the Cloudflare provider token
for provisioning, and set `operatorTunnelSecret` with `pulumi config set --secret`.
The tunnel secret must be base64 encoding at least 32 random bytes. The existing
operator ingress owner declares two exact Access applications, two proxied DNS
records and a local tunnel. Its rendered nonsecret config enforces the respective
Access audience at each origin and ends with a deny route. Validate live Access
and direct-origin isolation using the ingress runbook; local declaration tests
do not prove DNS or authentication on the real service.

## Transfer setup outputs

`pulumi stack output connectionProfile --json` produces only
`{host, host_public_key, identity_ref}`. Publish this nonsecret profile to the
deployment environment. Transfer the separately secret `deploymentPrivateKey`
output directly into that environment’s secret store under `identity_ref`, using
its stdin/import interface. Never put a private key in the profile, a release
manifest, CI artifacts or command logs. The key grants administrative SSH access.
Ordinary component releases require the profile and resolved SSH identity; they
require neither Pulumi state decryption nor cloud credentials.

Export `sharedResources`, `deploymentHandles`, and `observabilitySettings` separately with
`pulumi stack output NAME --json`. Transfer the resource plan and the matching
`infra/platform/platform_infra/resources.py` to a root-owned setup directory on
the host using the profile’s pinned SSH host key. On the host, run:

```sh
python3 /opt/clis-setup/resources.py --plan /opt/clis-setup/resources.json
```

This explicit platform preparation uses only Python’s standard library. It
checks that the pinned persistent device is mounted, validates installation
ownership and network isolation, then creates absent bridges and bind-backed
external volumes. It rejects an existing name owned by a different installation,
a public private bridge, a wrong backing directory, or a directory owner
mismatch. It never deletes persistent state. Re-running a matching plan is safe.
Use the exported environment handles in each independently owned Compose unit.
`observabilitySettings` contains the selected stack/host identities, private
metrics and Grafana-origin network handles, four external volume handles, both
`/` and `/srv/boxie` backing mounts. Playground currently has no metrics exporter.
Pass these settings to the observability owner’s renderer with explicit assets,
published collector digest, Grafana URL and the numeric journal GID read from
`/etc/boxie-platform/journal-group-gid` on the target host. The environment also
passes the canonical observability-owned `journald.conf` into host bootstrap;
its persistent policy, reader ACLs and bounded restart/flush are installed before
Docker. No product hostname is required to provision a generic Boxie host.
The selected Boxie and Playground outputs contain configuration and secret
references; resolve credentials only at the consuming process.

## Fresh installation and independent updates

1. Provision the host, firewall, volume and selected DNS; wait for cloud-init and
   the Docker/gVisor smoke to finish. Transfer the connection profile and secret
   SSH credential separately. Verify `/srv/boxie` against the exported device.
2. Prepare shared resources. Start [platform PostgreSQL](../../platform/README.md)
   and apply Boxie SQL explicitly. Set up [Temporal](../../../tools/boxie/infra/temporal/README.md)
   credentials/certificates, run its schema jobs and idempotent namespace bootstrap.
3. Release Temporal, [observability](../../observability/README.md), and selected
   [operator ingress](../../ingress/README.md). Check private dependency readiness,
   telemetry and live Access protection before starting Boxie.
4. Release [Boxie](../../../tools/boxie/infra/README.md) with API and orchestrator
   on one approved image identity. Verify readiness and a generic
   create/terminal/stop flow before adding a product consumer.
5. Optionally release [Playground](../../../tools/cli-playground/infra/README.md)
   and verify its public product/preview routes.

Subsequent releases target one Compose unit and its declared dependencies. A
Playground release must not invoke Pulumi or update Boxie, Temporal, PostgreSQL,
observability or shared bridges/volumes. A Boxie release must not apply Temporal
schema upgrades or update Playground. Preserve persistent resources on failed
activation. Drain/terminate sessions for orchestrator changes; existing PTYs are not
restored by a host or orchestrator restart. One host remains a failure domain. This
project performs no migration of the old undeployed Playground Pulumi state,
application backup, disaster recovery or cross-host placement.

## Activation evidence and capacity gate

Local tests cover declarations and workload failure boundaries; they do not
certify a provisioned host. Before enabling automatic application releases:

- Run a provider-backed `pulumi preview` and first install on the selected host.
  Verify pinned SSH identity, fail-closed disk mounting after reboot, shared
  network ownership, reader-owned credentials and writable directories.
- Export `observabilitySettings` and use the observability owner's renderer. Its
  mount list includes `/` and `/srv/boxie`; the current Boxie and native infrastructure scrapes are retained. Verify persistent journal collection, both disk series,
  Grafana login/queries, Temporal UI and live Access/origin isolation.
- Verify Boxie create/terminal/stop, actual gVisor restrictions and pending-cleanup
  recovery before publishing Playground. Then verify native browser product flow.
- With the complete stack running, exercise eight concurrent sandboxes and record
  host memory, CPU, filesystem headroom, telemetry drops and provisioning/cleanup
  latency. Inspect `docker stats --no-stream`, the provisioned dashboards and
  `df -h / /srv/boxie`; retain the observations with the environment's activation
  record. Reduce configured capacity or select a larger host if headroom is
  insufficient. The example host type is a starting configuration, not a sizing
  certification.

A failed application update preserves current/previous releases and persistent
state. Inspect component `status.json`, Docker health and the local journal when
the dashboard is unavailable. Reconcile owned pending cleanup before freeing
capacity. Fix dependency or credential configuration explicitly; do not delete
volumes, bypass storage guards or roll back Temporal schemas. Configuration and
secret files referenced by retained application releases should use versioned
host paths; changing a file in place also changes what an old release can read.
