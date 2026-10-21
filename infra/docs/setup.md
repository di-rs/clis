# Set up the shared host

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
[Single-host Sanctuary setup](../../.github/workflows/sanctuary-setup.yml)
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
  "root@$host" 'cat /etc/sanctuary/cli-playground/preview-token' > "$private/token"
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
