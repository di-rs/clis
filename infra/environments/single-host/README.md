# Install and operate the shared host

Use this guide to set up Boxie and cli-playground on one Hetzner server, then
inspect a failed release. Pulumi creates the host, disk, DNS and operator Access
applications. The setup workflow copies the Compose projects and starts them.
Routine releases run over pinned SSH from GitHub Actions.

Existing installations must first follow the [component naming cutover](../../../docs/component-naming-cutover.md).
It preserves configuration, credentials and data while adopting the new service
names. Do not use fresh setup to bypass that migration.

## Prepare the Sanctuary environment

Create GitHub environments `sanctuary`, `boxie`, `cli-playground` and
`cli-playground-preview`. Restrict deployment to `master`. The Sanctuary environment
holds the following provisioning inputs:

| Setting | Purpose |
| --- | --- |
| Variable `PULUMI_BACKEND_URL` | Existing R2 S3 backend URL, including its endpoint |
| Secrets `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Read/write access to the dedicated state bucket |
| Secret `PULUMI_CONFIG_PASSPHRASE` | Encrypt state secrets; retain it with the state backup |
| Secret `HCLOUD_TOKEN` | Read/write access to the dedicated Hetzner project |
| Secret `CLOUDFLARE_API_TOKEN` | Provision DNS, tunnel and Access applications |
| Secret `CLOUDFLARE_DNS_API_TOKEN` | Zone-scoped DNS edit/read access for ACME |
| Variable `SINGLE_HOST_CONFIG` | Nonsecret JSON configuration below |

The state bucket must exist before setup. Keep its passphrase and backups outside
the VM. Use a dedicated Cloudflare zone where possible because the ACME token can
edit records in its zone. Do not create a new Pulumi state for an existing host.

Example `SINGLE_HOST_CONFIG` (replace the placeholders):

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
(default `cx33`), `diskSize` (default 80 GiB) and `protect` (default true). The
host accepts public-key SSH and exposes only SSH, HTTP, HTTPS and ICMP. Boxie and
Temporal stay on private Docker networks. Narrow `sshCidrs` only if both operators
and Actions runners can reach the host.

## Run setup

Grant this repository Actions access to its private GHCR packages. Run
[Single-host Sanctuary setup](../../../.github/workflows/sanctuary-setup.yml)
from `master`. This manual workflow builds Boxie, the cli-playground backend and
sandbox, and the collector from current `master`; it does not ask for image
digests. It then runs Pulumi, waits for cloud-init, copies the committed files
and private settings over SSH, and starts the Compose projects. It can create
paid cloud resources.

Setup generates internal credentials and certificates, initializes PostgreSQL and
Temporal, starts Boxie and its monitoring and tunnel, then starts cli-playground and
its proxy. A rerun keeps existing credentials and data. Partial credentials cause
setup to fail for inspection. PostgreSQL and Temporal server/schema upgrades stay
separate from application releases. Cloud-init handles first boot; later edits to
host bootstrap code do not reinstall an existing server. On a new host,
`sshPublicKey` is added to root's authorized keys during cloud-init. Changing it
later does not change an existing server; rotate that key on the host. The
generated deployment and host keys are protected with the server. Replacing either
key resource alone would make Pulumi's SSH outputs disagree with the live host.
Rotate them as part of a planned host replacement, then repeat the GitHub SSH
credential handoff below.

A successful setup run does not establish production readiness by itself. Verify
DNS, ACME, Access rules, native runsc lifecycle, host capacity and an off-host
backup restore before enabling release gates.

## Give application workflows their SSH credentials

Run the following locally from the repository root with your own authenticated
Pulumi and `gh` sessions. Select the existing `sanctuary` production stack first.
The application environments receive host-level root SSH authority, not
per-application host isolation. They receive no R2 or provider credentials.

```bash
set -euo pipefail
set +x
umask 077
repository=di-rs/sanctuary
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
  "root@$host" 'cat /etc/sanctuary/boxie/preview-token' > "$private/token"
[[ $(cat "$private/token") =~ ^[a-f0-9]{64}$ ]]
for environment in boxie cli-playground; do
  gh variable set DEPLOY_HOST --repo "$repository" --env "$environment" --body "$host"
  gh secret set DEPLOY_SSH_KEY --repo "$repository" --env "$environment" < "$private/key"
  gh secret set DEPLOY_KNOWN_HOSTS --repo "$repository" --env "$environment" < "$private/known_hosts"
done
gh variable set CLI_PLAYGROUND_URL --repo "$repository" --env cli-playground-preview --body "$url"
gh secret set CLI_PLAYGROUND_DEPLOY_TOKEN --repo "$repository" --env cli-playground-preview < "$private/token"
```

All retrieved values are checked before the first GitHub write. If a GitHub write
fails, rerun the handoff after correcting permissions; overwriting the same
settings is safe. Setup never receives your GitHub administration credential.
Registry authentication uses the workflow's temporary package token, stored in
temporary files under `/run`; it expires with the token's lifetime.

## Release and inspect failures

Run either release manually from Actions on `master`:

- [🚀 cli-playground Release](../../../.github/workflows/cli-playground-release.yml)
  builds and deploys the cli-playground backend and frontend together in the backend image.
- [🚀 Boxie and Sandbox Release](../../../.github/workflows/boxie-release.yml)
  builds the Boxie image and sandbox from one commit, then updates Boxie followed
  by the sandbox profile. It does not release the cli-playground application.

A failed stage stops the remaining deployments in its workflow; completed stages
are not rolled back automatically. Boxie stages use the `boxie` environment;
the cli-playground release uses `cli-playground`.
Each stage calls the shared [release transport](release.sh) for SSH, image settings
and temporary registry credentials; service scripts own migration and readiness checks.

The Boxie stage pulls its image, stops API and supervisor, runs Alembic migration,
and starts both services with the metrics overlay. PostgreSQL and Temporal stay
up. Authenticated readiness must pass before the next stage. Pending requests may
resume after the restart; an empty capacity table is not a release condition.
The separate native host acceptance run covers terminal allocation and cleanup.

The sandbox stage changes the approved cli-playground profile digest and recreates
Boxie's supervisor, preserving other profile fields. The backend stage pulls
and recreates only `backend`; its proxy stays up.

Setup and release workflows share one `sanctuary-production` queue. Each run selects
current `master` after entering the queue, then builds and deploys that checkout.
If a run fails, choose **Re-run all jobs** or start a new workflow run. Re-running
only failed jobs keeps the earlier source selection and is rejected before
deployment or provisioning starts.

Builds can run before production activation; deployments still require
`DEPLOYMENT_ACCEPTANCE_PASSED`. Pushes do not
trigger releases.

An unsuccessful SSH or Compose command fails the Actions run. On the host, inspect
services and recent logs before rerunning the workflow:

```sh
docker compose --env-file /etc/sanctuary/boxie.env --env-file /etc/sanctuary/boxie-image.env \
  -f /opt/sanctuary/boxie/compose.production.yml -f /opt/sanctuary/boxie/compose.operations.yml \
  -f /opt/sanctuary/boxie/compose.observability.yml ps
docker compose --env-file /etc/sanctuary/boxie.env --env-file /etc/sanctuary/boxie-image.env \
  -f /opt/sanctuary/boxie/compose.production.yml -f /opt/sanctuary/boxie/compose.operations.yml \
  -f /opt/sanctuary/boxie/compose.observability.yml logs --tail 100 api supervisor
docker compose --env-file /etc/sanctuary/cli-playground.env --env-file /etc/sanctuary/cli-playground-image.env \
  -f /opt/sanctuary/cli-playground/compose.production.yml ps
```

A migration failure leaves Boxie's API and supervisor stopped. Check migration
output and database state before retrying; do not start an older application
against a newer schema. After an SSH interruption, first check whether Compose
finished the operation and whether the application is healthy. Fix the cause and
rerun the workflow. There is no automatic rollback or separate deployment state
to clear.

## Acceptance and maintenance

Run local checks from the repository root:

```sh
uv sync --locked --all-packages
uv run --locked pytest infra/host/tests infra/environments/single-host/tests \
  packages/boxie/tests/test_deployment.py tools/cli-playground/tests/test_deployment.py \
  tools/cli-playground/tests/test_infra_workflows.py
```

On an authorized native Linux amd64 host, exercise fresh setup, an unchanged
rerun, both release workflows, migration failure and retry, metrics, tunnel
configuration and preview publication. The local command tests cannot establish
those host behaviors. The [host requirements](../../../packages/boxie/infra/host-requirements.md)
include the runsc and backup checks.

The generated Boxie TLS leaf certificates last 90 days and the CAs last 365 days.
Plan renewal separately. Maintain an off-host backup and rehearse restoration
before relying on recovery; the protected disk alone is not a
backup.
