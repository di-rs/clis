# PR preview lifecycle and workflow structure

The user approved PR subdomains, explicit preview/build states, and uninterrupted
revision-pinned sessions after reviewing the deployment implementation. No
infrastructure has been provisioned; hosting starts with the initial setup flow.
Keep the persistent self-hosted gateway and lazy per-terminal gVisor sandboxes.
PR publication updates stored data; it never provisions a host or redeploys the
runtime. Existing terminal lifetime and resource limits remain in force.

## Preview identity and lifecycle

Each PR keeps its existing deterministic preview identifier. Production links
use `https://pr-N.<control-host>/p/<identifier>`; existing control-host paths and
legacy hash subdomains continue working. Host validation binds each PR hostname
to its matching preview identifier. Authenticated mutations remain confined to
the control hostname.

Store latest build state separately from the last runnable artifact. Public
metadata retains `id`, `pr`, `clis`, `sha`, and ISO `expires_at`, and adds
`revision` and `build`. Without runnable artifacts, `sha` and `revision` are null
and `clis` is empty. `build` is null for legacy/manual publications, otherwise
`{sha, run_id, run_number, run_attempt, state}`. States are `building`, `ready`,
`failed`, `empty`, and `cancelled`.

Trusted CI sends `PUT /api/pull-requests/{pr}/build` with the build fields.
`ready` is assigned only after successful artifact publication. Uploads include
the same run identity. Compare `(run_number, run_attempt)` lexicographically;
bind each tuple to its SHA and run ID. Late progress cannot downgrade a terminal
state, and same-run failure cannot downgrade ready. Recheck ordering after
archive staging before committing the publication. Reject stale/conflicting
updates with HTTP 409. Legacy local uploads remain supported.

Artifact revision is `sha-run_number-run_attempt` for CI and `sha` for manual or
legacy bundles. Each version is immutable; a new attempt at the same source SHA
can publish different bytes without changing a running session's files. New
WebSockets pin both SHA and artifact revision. Existing sessions retain their
original Environment and directory across publications. Garbage collection keeps
current artifacts and all directories referenced by active or pending-cleanup
sessions, then removes unused versions. Closing or expiring a PR still ends its
sessions. Runtime restarts still end terminals; seamless runtime upgrades are
outside this change.

Building, failed, and cancelled states retain the last runnable artifact and
label it explicitly. Empty means no affected CLIs: it removes availability for
new sessions while existing sessions finish. Flat on-disk metadata remains
readable. Build-only records expire after seven days.

## Browser behavior

Display latest build status and source commit independently of the running
terminal commit. Poll metadata every ten seconds while the page is open, without
recreating the terminal or changing its CLI selector. A ready newer artifact
offers an explicit switch that resets the session. A first build that becomes
ready can open the initial terminal automatically. Failures without a runnable
artifact show an explanatory state; failures with an older artifact display a
clear last-successful-build notice. Temporary polling failures preserve a working
terminal. Reset/reconnect reloads metadata and uses the latest runnable artifact.

## Production subdomains

Initial setup creates control and wildcard DNS-only A records pointing to the
same retained IPv4. Traefik requests a certificate for the control host and its
wildcard through Cloudflare DNS-01. Set `PLAYGROUND_PREVIEW_DOMAIN` on the gateway.
Only the proxy receives `CF_DNS_API_TOKEN`, supplied from the dedicated
`CLOUDFLARE_DNS_API_TOKEN` playground environment secret through the private SSH
configuration path. The provisioning token remains setup-only. The renewal
token needs Zone Read and DNS Edit scoped to the selected zone; separate tokens
permit independent rotation, not per-record privilege reduction.

Configure the required credentials and run manual setup after merging. Setup
creates the DNS records before deploying the runtime, then verifies public HTTPS
for both the control hostname and `pr-1.<control-host>`.

## Workflows

Reduce seven playground workflows to five:

1. Build: untrusted PR-head code builds affected binaries; preserve filename and
   workflow name because the trusted controller verifies them.
2. Preview: combine publication and close/reopen reconciliation in one trusted
   default-branch controller; handle build progress and completion events.
3. Release: build runtime images and deploy them in the same reusable workflow;
   inline the former deployment-only workflow and preserve its production lock.
4. Setup: manually provision infrastructure, then invoke release.
5. Sandbox: retain the independent Linux/gVisor verification workflow.

Keep benchmark execution/reporting, Git policy, CodeQL, maintenance and ordinary
PR checks in their existing trust/schedule boundaries. No push/master benchmarks.

## Verification

Exercise stale and reordered events, same-SHA reruns, failed/empty first builds,
legacy metadata, publications with live sessions, retained artifacts after failed
container cleanup, and host/path ownership. Browser checks must prove polling
does not close or replace a terminal and switching pins the new artifact.
Infrastructure checks cover wildcard DNS, proxy-only credential delivery and
both public health probes. Run Python tests, Ruff, strict mypy, frontend checks,
unit/browser tests, actionlint, zizmor, Markdown checks and an independent review.
Local tests cannot establish actual provider DNS or ACME issuance; no live
infrastructure is provisioned during this implementation.
