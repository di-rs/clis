# Install and operate the shared host

Use this environment to install Boxie and CLI Playground on one protected Hetzner
host. Pulumi owns the host, persistent disk, DNS and operator Access applications.
Its Command provider copies the installation and starts a bounded systemd unit.
Routine releases use direct `scp` and `ssh` in Actions.

Production activation still requires native Linux acceptance and an off-host
backup/restore rehearsal. Keep `DEPLOYMENT_ACCEPTANCE_PASSED`,
`BOXIE_BACKUP_RESTORE_VERIFIED`, `BOXIE_AUTO_DEPLOY` and `PLAYGROUND_AUTO_DEPLOY`
unset until the corresponding evidence exists. The application release workflows
check the first two gates even for manual runs. See the
[Boxie runtime requirements](../../../tools/boxie/infra/runtime.md).

## Prepare the platform environment

Create GitHub environments `platform`, `boxie`, `playground` and
`playground-preview`. Restrict platform and application deployment to `master`;
use required reviewers where appropriate. Preview jobs run the default-branch
controller and treat PR binaries as data. Their environment must allow those
trusted workflow events.

The `platform` environment holds provisioning credentials:

| Setting | Purpose |
| --- | --- |
| Variable `PULUMI_BACKEND_URL` | Existing R2 S3 backend URL, including its endpoint |
| Secrets `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Read/write access to the dedicated state bucket |
| Secret `PULUMI_CONFIG_PASSPHRASE` | Encrypt state secrets; retain it with the state backup |
| Secret `HCLOUD_TOKEN` | Read/write access to the dedicated Hetzner project |
| Secret `CLOUDFLARE_API_TOKEN` | Provision DNS, tunnel and Access applications in the selected account/zone |
| Secret `CLOUDFLARE_DNS_API_TOKEN` | Zone-scoped DNS edit/read access for ACME renewal |
| Variable `SINGLE_HOST_CONFIG` | Nonsecret JSON configuration below |

Use a dedicated Cloudflare zone where possible: the renewal token can edit other
records in its zone. Keep the state bucket, passphrase and resource protection
when recovering failures. Do not recreate an existing installation in a new
state backend. Import or migrate existing resources explicitly before adopting
this `clis` Pulumi project.

Example `SINGLE_HOST_CONFIG` (replace every placeholder):

```json
{
  "domain": "play.example.com",
  "cloudflareZoneId": "00000000000000000000000000000000",
  "cloudflareAccountId": "00000000000000000000000000000000",
  "grafanaHostname": "grafana.example.com",
  "temporalHostname": "temporal.example.com",
  "operatorTeamName": "example-team",
  "operatorEmails": ["operator@example.com"],
  "sshCidrs": ["0.0.0.0/0"],
  "acmeEmail": "operator@example.com"
}
```

Optional settings are `sshPublicKey`, `location` (`nbg1` or `fsn1`), `serverType`
(default `cx33`), `diskSize` (default 80 GiB) and `protect` (default true). SSH
allows only public-key authentication. Narrow `sshCidrs` only when both operators
and deployment runners have an allowed egress address. Only SSH, HTTP, HTTPS and
ICMP are exposed; the Boxie API and Temporal stay on private Docker networks.

## Run the initial installation

Publish approved Linux amd64 images for Boxie, the Playground gateway and sandbox,
and the [journal collector](../../../tools/boxie/infra/observability/README.md).
Grant this repository Actions access to private GHCR packages. Run
[Single-host platform setup](../../../.github/workflows/cli-playground-setup.yml)
from `master`, supplying all four registry-qualified `image@sha256:…` references.
This manual workflow can create paid resources.

Setup initializes credentials, certificates, PostgreSQL, Temporal schemas and
namespace, Boxie, monitoring, operator ingress, then Playground. It shares the
application deployment operations, including migration and readiness checks.
Reruns preserve complete credentials and database data. Partial credentials stop
setup for inspection. PostgreSQL and Temporal image/schema upgrades are explicit
maintenance; application releases do not upgrade those projects.

Cloud-init prepares the first boot. Explicit setup installs updated operation
units and runs `systemctl daemon-reload` under the host lock. It is not a substitute
for host OS or certificate maintenance. Review host replacement plans before
applying changes to cloud-init; protected resources refuse destructive changes.

A successful workflow establishes only the checks it ran. Confirm public DNS,
ACME, Access restrictions, resource capacity and off-host restoration on the
selected host before enabling production release gates. See [acceptance](#acceptance).

## Give application workflows their SSH credentials

Run the following locally from the repository root with your own authenticated
Pulumi and `gh` sessions. Select the existing `clis` production stack first.
The application environments receive host-level root SSH authority, not
per-application host isolation. They receive no R2 or provider credentials.

```bash
set -euo pipefail
set +x
umask 077
repository=di-rs/clis
private=$(mktemp -d)
trap 'rm -rf "$private"' EXIT
pulumi -C infra/environments/single-host stack select production --non-interactive
host=$(pulumi -C infra/environments/single-host stack output ipv4)
url=$(pulumi -C infra/environments/single-host stack output publicUrl)
pulumi -C infra/environments/single-host stack output sshPrivateKey --show-secrets > "$private/key"
public_key=$(pulumi -C infra/environments/single-host stack output sshHostPublicKey)
python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$host"
[[ $url =~ ^https://[a-z0-9.-]+$ ]]
printf '%s %s\n' "$host" "$public_key" > "$private/known_hosts"
ssh-keygen -y -f "$private/key" >/dev/null
ssh-keygen -l -f "$private/known_hosts" >/dev/null
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$private/known_hosts" -i "$private/key" \
  "root@$host" 'cat /etc/clis/boxie/preview-token' > "$private/token"
[[ $(cat "$private/token") =~ ^[a-f0-9]{64}$ ]]
for environment in boxie playground; do
  gh variable set DEPLOY_HOST --repo "$repository" --env "$environment" --body "$host"
  gh secret set DEPLOY_SSH_KEY --repo "$repository" --env "$environment" < "$private/key"
  gh secret set DEPLOY_KNOWN_HOSTS --repo "$repository" --env "$environment" < "$private/known_hosts"
done
gh variable set PLAYGROUND_URL --repo "$repository" --env playground-preview --body "$url"
gh secret set PLAYGROUND_DEPLOY_TOKEN --repo "$repository" --env playground-preview < "$private/token"
```

All retrieved values are checked before the first GitHub write. If a GitHub write
fails, rerun the handoff after correcting permissions; overwriting the same
settings is safe. Setup never receives your GitHub administration credential.
Registry authentication uses the workflow's temporary package token, stored in
private release files; it expires with the token's lifetime.

## Release and inspect failures

[Boxie releases](../../../.github/workflows/boxie-release.yml) stop API admission,
verify sandbox deletion, migrate, start the orchestrator, reconcile capacity,
then start the API and exercise a real terminal lifecycle. Migration or readiness
failure leaves the operation pending for inspection.

[Playground releases](../../../.github/workflows/cli-playground-release.yml)
update only the gateway with `--no-deps`. A sandbox-image change first updates only
the approved profile's image field and restarts the current Boxie release. Profile
commands and other custom fields survive. Proxy changes require setup/maintenance.

Workflows build candidate images, then compare source and locked runtime
dependencies against the last successful host revision. Older or unrelated
revisions are rejected. The host checks the expected revision again under its
lock. Actions may replace pending runs; the design does not rely on FIFO order.

The workflow summary names the operation, for example `clis-boxie@123456-1.service`.
On the host, inspect it with:

```sh
systemctl show clis-boxie@123456-1.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus
journalctl -u clis-boxie@123456-1.service --no-pager
cat /srv/clis/deployment/pending /srv/clis/deployment/stage
readlink -f /srv/clis/deployment/boxie/current
```

Success requires `Result=success`, `SubState=exited`, zero exit status, the expected
revision in `current/revision`, and no pending marker. An SSH disconnect gives an
unknown outcome; inspect the unit before doing anything else. A running unit can
still finish successfully. The 900-second timeout terminates its control group.
Timeouts and reboots leave the persistent pending marker, blocking another mutation.

After a failed operation, identify its stage and inspect containers, database
migration state and capacity records. Confirm no unit still owns the host lock.
Only then remove `/srv/clis/deployment/pending` and retry with a new numeric
`run-id-attempt`. Never clear the marker merely because SSH disconnected.

For explicit recovery, copy a saved successful release into a **new** release
directory, keep its source SHA and image digest, and set `expected-revision` to the
currently recorded SHA. Check schema compatibility first; never downgrade the
database. Supply fresh registry authentication for private images. Start the
matching systemd instance and apply the same success checks. This operator path
allows an intentional older release; ordinary Actions deployment does not.
For a failed sandbox update, inspect `profiles.before.json` in the attempted
Playground release before deciding whether to restore that profile.

## Acceptance

Run the local contract tests from the repository root:

```sh
uv sync --locked --all-packages
uv run --locked pytest infra/host/tests infra/environments/single-host/tests \
  tools/boxie/tests/test_deployment.py tools/cli-playground/tests/test_deployment.py
uv run --locked python tools/boxie/tests/run_integration.py
```

On an authorized disposable native Linux amd64 host, run the
[runsc and browser tier](../../../docs/testing.md) and validate first installation,
unchanged rerun, independent updates, migration failure/recovery, SSH interruption,
unit timeout, missing-disk refusal and reboot persistence. Verify PR artifact
publication and close/reopen behavior against the resulting public URL. Fake Docker
command tests and Docker Desktop do not establish these host properties.

The opt-in systemd tests create uniquely named temporary units and remove them
on exit. On the disposable native host, run as root from this checkout:

```sh
CLIS_NATIVE_OPERATION_TESTS=1 uv run --locked pytest infra/host/tests/test_native_operation.py
```

They verify that an operation finishes after its initiating `systemctl` client
exits and that a shortened unit timeout kills children while retaining the
pending marker. They do not replace real SSH-disconnection or reboot testing.
