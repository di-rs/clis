# Playground hosting

One Hetzner CX33 in Nuremberg runs Python, Traefik and Docker with gVisor.
Trusted CI builds shared images; Python deploys approved digests over SSH.
PRs upload only affected CLI binaries. Previews expire after seven days or PR closure.

## 1. Provision the VPS

Install [Pulumi](https://www.pulumi.com/docs/install/) and run
`uv sync --locked --all-packages` from the repository root. Supply `HCLOUD_TOKEN`
and `PULUMI_CONFIG_PASSPHRASE` privately, then:

```sh
pulumi login file:///absolute/private/path/playground-state
cd tools/cli-playground/infra
pulumi stack init production
pulumi config set domain play.thesecret.dev
pulumi config set sshPublicKey "$(cat /absolute/path/to/id_ed25519.pub)"
pulumi config set --path 'adminCidrs[0]' 'YOUR_PUBLIC_IP/32'
pulumi preview
pulumi up
pulumi stack output ipv4
```

Defaults: Ubuntu 24.04, 4 vCPU / 8 GB RAM, retained IPv4, public 80/443,
SSH restricted to your CIDR, deletion protection enabled. Optional settings are
in [Pulumi.example.yaml](Pulumi.example.yaml). Keep Pulumi state outside the repo
and VPS, and back it up. No cloud credentials go into cloud-init.

## 2. Configure DNS and secrets

In Cloudflare's `thesecret.dev` zone, create **DNS-only** A records for `play` and
`*.play` pointing at the VPS IPv4. Do not add AAAA records; this host is IPv4-only.
Traefik obtains and renews the wildcard HTTPS certificate using DNS-01.

Copy [app.env.example](app.env.example) and [dns.env.example](dns.env.example) to
private files outside the checkout; fill them in and `chmod 600` both.

- Set your certificate contact email and generate `PLAYGROUND_DEPLOY_TOKEN`
  with `openssl rand -hex 32`.
- Give `CF_DNS_API_TOKEN` **Zone / DNS / Edit** and **Zone / Zone / Read**, scoped
  to `thesecret.dev`. [Cloudflare provider reference](https://go-acme.github.io/lego/dns/cloudflare/).
- Use literal `KEY=value` entries, without shell expressions or quotes.

Verify the server's SSH fingerprint through the Hetzner console, then connect
once with `ssh root@VPS_IPV4` to save the verified key in `known_hosts`.

## 3. Release the application

After merge, run the trusted [release workflow](../../../.github/workflows/cli-playground-release.yml)
on `master`. Review its successful run, commit checks and image digests:

```sh
gh workflow run cli-playground-release.yml --ref master
gh run list --workflow cli-playground-release.yml
gh run download RUN_ID --name cli-playground-release --dir /private/path/release
cat /private/path/release/release.json
```

GHCR packages start private. Make `clis-playground-gateway` and
`clis-playground-sandbox` public, or configure read-only registry access on the VPS.
Download after the latest successful attempt; reruns replace the manifest artifact.
Check out the manifest's exact commit, then run from the repository root:

```sh
uv run --locked python tools/cli-playground/infra/deploy.py \
  --host VPS_IPV4 \
  --release-file /private/path/release/release.json \
  --env-file /private/path/app.env \
  --dns-env-file /private/path/dns.env
```

The helper verifies SSH, the commit and image digests, pulls before changing live
configuration, then checks Compose and public HTTPS health. The VPS builds nothing.
SSH has root authority; SSH/cloud/DNS credentials stay outside the release workflow.
Approval is operator review of the trusted CI manifest, without signature enforcement.

Set GitHub variable `PLAYGROUND_URL=https://play.thesecret.dev` and secret
`PLAYGROUND_DEPLOY_TOKEN` to the same token as `app.env` to enable PR previews.
The publisher and lifecycle workflows must also be merged before they activate.

## gVisor and operations

[bootstrap.sh](bootstrap.sh) installs `runsc` and registers it with Docker using
`--platform=systrap`. [sessions.py](../playground/sessions.py) selects
`--runtime=runsc` for each terminal. Missing gVisor fails deployment; `runc` is an
explicit local-development override. The [Linux smoke workflow](../../../.github/workflows/cli-playground-sandbox.yml)
checks the real runtime, native CLI, shell operators, PTY and cleanup.

Run one gateway worker. Updates interrupt active terminals. Inspect
`cloud-init status --long`, `/var/log/cli-playground-deploy.log`, and
`docker logs` for failures. Private configuration lives in `/etc/cli-playground`;
releases in `/opt/cli-playground/releases`; certificates in `/var/lib/cli-playground-proxy`.

For rollback, keep the previous manifest and registry images, check out its commit
and rerun the helper with current private env files. CI retains manifests for 30
days; save approved ones separately. Rollback is manual. Back up Pulumi state,
secrets and certificates; remove obsolete release images deliberately. The retained
IPv4 remains billable until explicitly destroyed.
