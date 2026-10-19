# Playground research and design

Research date: 2026-10-13. The goal is an easy PR review environment for the actual
Rust CLIs, including their filesystem and interactive terminal behavior. Python
owns tooling and the gateway under `tools/`; a Svelte/TypeScript static SPA renders
the terminal. The first version should be deployable without adopting a platform
framework or rewriting existing Rust programs.

## Options investigated

| Approach | Advantages | Costs and decision |
| --- | --- | --- |
| Browser WASI + static hosting | No shell server, local virtual files, inexpensive preview hosting | Existing CLI compatibility fails; not the first version. |
| Wasmer WASIX + browser terminal | Richer processes/filesystem than standard WASI | Custom Rust toolchain and compatibility work; keep as a future experiment. |
| Native containers + Python/WebSocket terminal | Runs the existing binaries and Bash; supports Kara | Selected with Docker + gVisor on a dedicated VPS. |
| Managed preview service per CLI | Provider handles URLs and lifecycle | Excluded: previews must run on our own server. |

[Standard Rust WASI](https://doc.rust-lang.org/rustc/platform-support/wasm32-wasip1.html)
supports command-style programs but lacks ordinary process/thread spawning.
[browser_wasi_shim](https://github.com/bjorn3/browser_wasi_shim) provides a small
in-memory filesystem and WASI imports, not a Linux terminal environment.
The [Wasmer JavaScript SDK](https://docs.wasmer.io/runtime/js/) supplies a richer
browser runtime with workers, streams and files. It requires cross-origin isolation
and SharedArrayBuffer; building existing Rust software for WASIX introduces the
[WASIX toolchain](https://www.wasix.org/docs/language-guide/rust/installation/).

Real probes of this workspace, without changing Rust code, found:

- `grepr`: standard WASI compilation fails in `ctrlc`.
- `lsr`: compilation fails in `uzers`; it also uses Unix metadata APIs.
- `kara`: `crossterm` terminal/raw-mode support fails to compile.
- `biggie`: Unix `MetadataExt` operations fail to compile.
- `echor`: compilation succeeds, but running the module under Node WASI panics
  because the shared logger calls `std::process::id()` even when logging is off.

This is why the native backend is a compatibility choice, not a claim that browser
WebAssembly cannot support CLI playgrounds in general.

[ttyd](https://github.com/tsl0922/ttyd) was also considered: it already combines a
PTY and browser terminal. For this version, [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
and [xterm.js](https://xtermjs.org/) keep preview registration, session policy and
terminal lifecycle in one Python service. There is no custom shell parser.

Frontend decision updated 2026-10-14: use Svelte, strict TypeScript and
[Vite+](https://viteplus.dev/guide) for a component-based static frontend. Its
[static Docker build pattern](https://viteplus.dev/guide/docker) lets the Python
runtime serve compiled assets without Node. SvelteKit, SSR and Tailwind are
excluded; see the [frontend constraints](README.md).

## Hosting and CI

[Netlify deploy previews](https://docs.netlify.com/deploy/deploy-types/deploy-previews/)
would be a good fit for static browser execution. Cloudflare's native
[GitHub integration](https://developers.cloudflare.com/pages/configuration/git-integration/github-integration/)
does not create previews for fork PRs. GitHub Pages has one repository site and
its [deployment action](https://github.com/actions/deploy-pages) does not offer a
generally available native PR-preview mode. None solves native shell execution
without a backend. [Render preview environments](https://render.com/docs/preview-environments)
provide container previews with account/plan and compute requirements, but a
service per CLI is more infrastructure than this first version needs.

One dedicated Linux host runs the gateway and creates short-lived Docker/gVisor
containers. One stable PR URL selects its current batch of affected CLIs; opening
it creates a fresh session with all those binaries. Previews use
`https://pr-N.play.thesecret.dev/p/<identifier>`. A control DNS record, wildcard
record and wildcard certificate cover the application; no per-PR DNS record, registry
publication or VM provisioning is needed.

Infrastructure decision updated 2026-10-14: Python Pulumi provisions a Hetzner
CX33 in Nuremberg, a retained IPv4 address and a firewall. Cloud-init installs
Docker and gVisor. The default permits public IPv4 SSH with key-only authentication
so changing GitHub-hosted runner addresses can deploy without firewall changes.
Pulumi generates deployment and server host keys, encrypts private key material
in state, and supplies the host identity through cloud-init. CI pins that identity
when connecting.

Setup is browser-only: create a private Standard R2 bucket manually once
(EU jurisdiction recommended), store its bucket-scoped S3 credentials and the
state encryption passphrase in the `master`-restricted GitHub environment, then
run **☁️ Playground infrastructure setup**. R2 state remains outside the VPS lifecycle. The
setup workflow applies infrastructure and installs services. Later updates use
the manual [Boxie Runtime and Sandbox Release](../../.github/workflows/cli-playground-release.yml)
workflow. Runtime deployment uses the configured SSH identity and no provider
administration tokens or Pulumi update.

Trusted default-branch CI builds the shared gateway and shell images and deploys
their immutable digests over verified SSH. The VPS pulls images without building
them. Cloudflare manages the `thesecret.dev` zone and DNS-only `play` and `*.play`
A records for the host's IPv4; the host has no IPv6 or AAAA record. Traefik obtains
and renews the control/wildcard certificate through DNS-01 with a dedicated
zone-scoped renewal token. Only the proxy receives that runtime credential.
See the [hosting guide](infra/README.md) for exact
account, token and workflow steps, and for rerun and recovery limits. No live
host or DNS resources have been provisioned by this change.

GitHub documents that [`workflow_run`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)
can access secrets and write tokens even when the triggering workflow cannot.
The design follows the repository's existing benchmark-report pattern: PR code
builds with read-only permissions, while default-branch code validates the run,
current PR revision and bounded artifacts before uploading one ZIP and posting
one preview link. The transport is shared with benchmark reporting. It never
executes PR code with deployment credentials.

## Session boundary

Docker exposes explicit [CPU/memory/PID limits](https://docs.docker.com/engine/containers/resource_constraints/),
a [network-none mode](https://docs.docker.com/engine/network/drivers/none/), and
[capability/no-new-privilege settings](https://docs.docker.com/engine/containers/run/).
Writable [tmpfs mounts](https://docs.docker.com/engine/storage/tmpfs/) hold mock
files. The gateway grants each session only its PR’s read-only binary directory;
it does not mount the repository, deployment token, or Docker socket into a session.
The async `aiodocker` client owns Docker transport and terminal attachment.
A WebSocket disconnect destroys the container, and an in-container wall-clock
limit bounds its life even if the gateway crashes.

[xterm's security guidance](https://xtermjs.org/docs/guides/security/) informs the
client: self-host scripts, check WebSocket origins, treat terminal data as terminal
data rather than HTML, and avoid attaching privileged browser integrations.
gVisor adds a userspace kernel boundary through `runsc`; its `systrap` platform
works without nested virtualization. The gateway still has host-level Docker
authority, so use a dedicated host. Production startup reconciles leftover
service-labelled containers before accepting sessions. Actual gVisor behavior
must be checked by the Linux smoke workflow and on the selected VPS.

## Agreed defaults and remaining iteration

1. **Hosting:** own Hetzner VPS, Python Pulumi with private R2 state, Docker +
   gVisor; no managed sandbox dependency. Use `play.thesecret.dev` with control
   and wildcard Cloudflare DNS records and DNS-01 HTTPS. Configure accounts in their websites;
   GitHub Actions handles setup and runtime releases.
2. **Access:** public preview links; authentication can be added if usage demands it.
3. **Retention:** seven days plus PR-close cleanup; files remain disposable.
4. **Coverage:** affected Rust CLIs; frontend/infrastructure-only changes do not
   rebuild all native binaries.

The [preview lifecycle](README.md) exposes explicit build states and preserves each running terminal across new PR
uploads. Its five workflows separate PR building, trusted preview reconciliation,
runtime releases, infrastructure setup and sandbox verification. Measure real
concurrency, memory and startup time before changing host capacity or adding more
orchestration.
