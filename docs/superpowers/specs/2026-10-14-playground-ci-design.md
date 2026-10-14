# Playground setup and runtime releases

The user approved a simpler architecture after reviewing the earlier draft.
R2 is created once through the Cloudflare dashboard. A manually triggered setup
workflow applies Pulumi infrastructure and invokes the same runtime release path
used by relevant pushes to master. PR binaries continue to use the existing
trusted HTTPS publisher and lazy, per-terminal gVisor sandboxes.

## Ownership

Pulumi manages the Hetzner server, retained IPv4, a static firewall, SSH identities
and one Cloudflare DNS-only A record. Cloud-init installs Docker, Compose and
gVisor on first boot. Port 22 permits key-only SSH; there are no temporary firewall
rules. Private SSH keys remain encrypted in R2. Both setup and release runners can
read those connection details; runtime releases receive no Hetzner or Cloudflare
API token and never run pulumi up.

Production uses one hostname with path-based previews. Traefik uses HTTP-01, so no
wildcard DNS or DNS API credential is installed on the VPS. The gateway and
sandbox images are immutable GHCR digests built from the workflow commit.
Deployment reuses the existing SSH/Compose helper with pinned host identity and
a temporary GHCR credential file. Runtime updates preserve artifact storage and
end active terminals. Rollback is a reviewed code revert followed by deployment.

## Workflows

- Setup: manual master-only workflow; validate configuration, login to R2,
  create/select production stack, preview/update, then invoke release workflow.
- Release: relevant pushes to master, manual dispatch, or reusable invocation;
  build gateway and sandbox images and call the reusable deployment workflow.
- Deploy: reusable workflow receives revision and digests, reads SSH details
  from existing R2 state, deploys the exact commit and verifies HTTPS. Provision
  and deploy jobs share a concurrency group; runtime release workflows are serialized.
- Existing PR build, publish and cleanup workflows remain separate.

Non-secret machine settings are tracked in Pulumi.production.yaml; domain, zone,
backend URL and certificate email are GitHub variables. Provider/state secrets
live in the master-restricted playground environment. PLAYGROUND_DEPLOY_TOKEN is
a repository secret shared by the publisher and runtime configuration.

## Verification and documentation

The user requested implementation first and tests afterward. Update package-owned
Python tests after the interfaces settle, then run the Python suite, Ruff, mypy,
workflow syntax/security checks, documentation checks and independent review.
Provide a browser-only step-by-step R2, domain and GitHub setup guide. No paid
cloud resources are created during implementation.
