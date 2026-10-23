# Publish pull-request previews

## Enable PR comments and live previews

Benchmark comments need no custom secret. The
[reporting workflow](../../../.github/workflows/cli-bench-report.yml) uses GitHub's
automatic `GITHUB_TOKEN` with `contents: read`, `actions: read` and
`pull-requests: write`. It reports completed benchmark jobs for the PR's current
commit. A run with no selected suites leaves the existing comment unchanged.

For a live cli-playground link, first deploy the backend and its Boxie sandbox using
the [shared-host setup guide](../../../infra/README.md).
The backend must be reachable from Actions over HTTPS, with working preview DNS
and certificates. In repository **Settings → Environments**, create
`cli-playground-preview` and allow deployments from `master`. Add these settings to
that environment:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `CLI_PLAYGROUND_URL` | The backend's public HTTPS origin, for example `https://play.example.com`, without a path, query or trailing slash. Use the same origin as the backend's `CLI_PLAYGROUND_PUBLIC_URL`. |
| Secret | `CLI_PLAYGROUND_DEPLOY_TOKEN` | The exact token configured as `CLI_PLAYGROUND_DEPLOY_TOKEN` on the backend. Shared-host setup stores it at `/etc/sanctuary/cli-playground/preview-token`. |

Use the shared-host guide's
[credential handoff](../../../infra/docs/setup.md#give-application-workflows-their-ssh-credentials)
to retrieve the existing token over pinned SSH and copy it into GitHub. Creating
an unrelated token in GitHub will cause authentication to fail. Settings placed
only in the `cli-playground` release environment are unavailable to preview jobs.

If you already have the token in a private local file, set the two values with an
authenticated `gh` session that can manage repository environment settings:

```sh
gh variable set CLI_PLAYGROUND_URL --repo di-rs/sanctuary --env cli-playground-preview \
  --body 'https://play.example.com'
gh secret set CLI_PLAYGROUND_DEPLOY_TOKEN --repo di-rs/sanctuary --env cli-playground-preview \
  < /private/path/preview-token
```

Replace the example origin and file path. The
[preview workflow](../../../.github/workflows/cli-playground-preview.yml) also
uses the automatic GitHub token with `contents: read`, `actions: read`,
`pull-requests: write` and `deployments: write`; no personal access token is needed
by either publisher. PR builds receive neither the preview token nor host SSH
credentials. If the environment requires approval, publication waits for it.

### Verify publication

Merge publisher changes into `master` before triggering new builds: publication
runs trusted default-branch code. Push a CLI change to a ready PR, or add `run-ci`
to a draft to enable the [PR build workflow](../../../.github/workflows/cli-playground.yml).
An ordinary PR description edit does not trigger cli-playground builds.

After the benchmark and cli-playground build workflows complete, check their separate
**CLI benchmark reporting** and **cli-playground PR previews** runs in Actions. The PR
should have a benchmark results comment and a cli-playground comment with a working
preview link. Open the preview and run the changed CLI to verify the sandbox.

If either cli-playground setting is absent, the publisher posts **Configuration
needed** with build artifact links instead of a live preview. A green build alone
does not prove publication succeeded. Check publisher logs for skipped stale
commits, and check the `cli-playground-preview` environment for pending approval or
missing settings. After configuring the environment, rerun the cli-playground binary
build for the current PR commit to trigger publication again.

## Enable generated command catalogs

Keep the repository variable `CLI_PLAYGROUND_CATALOG` unset until the compatible
publisher is on `master` and the gateway has been upgraded. Roll out in this order:

1. Deploy the backend release with the additive `0003_cli_catalog` migration.
   The deployment applies Alembic before starting the new service; existing
   binary-only revisions and upload intents keep null catalog fields.
2. Verify readiness and open an existing preview. Its terminal must work while
   the explorer reports **Command reference unavailable**.
3. Enable catalog generation in the unprivileged PR build by setting the
   **repository** variable, not the preview environment variable:

   ```sh
   gh variable set CLI_PLAYGROUND_CATALOG --repo di-rs/sanctuary --body '1'
   ```

4. Rerun the current PR build. Verify the catalog endpoint, command insertion,
   and the unchanged terminal connection when switching CLIs. Initial merge bases
   without exporter support correctly show **Comparison unavailable**.

Unsetting the variable stops new catalog production. Leave the compatible backend
running: published metadata and immutable revision identities remain valid. Do not
replace a catalog under an existing revision; use a new workflow run attempt.

### Build and comparison contract

With the variable set to `1` (or the local `build --catalog` option), the build
exports metadata from the exact copied head executables, resolves `git merge-base`
for the supplied base and head commits, and builds corresponding baseline CLIs in
an isolated source directory. Export invokes each binary with exactly `--help`
and `SANCTUARY_CLI_SCHEMA=1`, before its normal parsing or logging. Each capture
has a ten-second deadline and a 1 MiB output limit. Its working directory is
empty and its environment excludes ambient credentials. Execution occurs only
in the unprivileged build job; the process bounds are not a sandbox.

The baseline build has a ten-minute deadline. A missing exporter, failed baseline
build, or invalid baseline export yields an unavailable comparison. A CLI absent
from the merge base is added. A failed head export fails the catalog-producing
build, rather than publishing an incomplete reference. `true` and `false` export
explicit empty interfaces while retaining their normal exit behavior.

The flat binary ZIP may include one `catalog.json` sidecar, at most 4 MiB including
its canonical representation. Its version 1 envelope records `head_sha`,
`base_sha`, and one entry per published executable with its name, SHA-256 digest,
head command tree, optional baseline tree, and comparison availability. The
[shared exporter](../../../utils/cli-schema/README.md) defines the tree fields.
The [catalog validator](../src/cli_playground/catalog.py) owns structural limits:
up to 1,024 CLIs, depth 16, 4,096 command nodes and 32,768 arguments across both
trees, with bounded strings and collections. Unknown versions and fields,
duplicate identities, invalid names, and unsafe control characters are rejected.

Trusted publication and backend admission validate the sidecar against the
expected head SHA and the exact executable names and digests. They never execute
uploaded binaries. These checks bind descriptions to an artifact; they cannot
prove that untrusted PR code describes itself honestly. The recorded merge-base
SHA is a comparison identity, not independent evidence of behavior.

The gateway strips the sidecar from the executable ZIP sent to Boxie and stores
its canonical digest and contents with the upload intent and immutable revision.
A retry with different metadata conflicts even when the binaries are unchanged.
[Recovery](database.md) preserves the same catalog through interrupted publication.

### Retrieve a revision's catalog

`GET /api/environments/{id}/revisions/{revision}/catalog` returns the versioned
catalog with status 200, or JSON `null` for a legacy binary-only revision. It is
public under the same preview-link access model as build metadata. Unknown,
expired, or closed previews and unavailable revisions return 404. Revision IDs
are the SHA, or `SHA-run_number-run_attempt` for workflow builds.

Catalog retrieval is separate from build-status polling. The browser fetches it
when it opens or explicitly switches builds, and on **Retry**. Matching uses
argument IDs within a command path, then unambiguous public spellings. Command
renames can match through public aliases; ambiguous renames become additions and
removals. Ordering and whitespace-only prose changes are ignored, as is generated
usage layout. Explicit usage overrides, short/full help, flags, operands, defaults,
choices, requiredness, value counts, and global ownership are represented.

## Validation

Build the real frontend before `uv run pytest
packages/boxie/tests/test_packaging.py -m integration`. The required PR frontend job
checks isolated packaging and static responses. The separate
[shared-system lifecycle workflow](../../../.github/workflows/sanctuary-lifecycle.yml)
checks native runsc security and both generic and product terminals through the
private API. Missing runsc is a failure, never a successful runc substitute.
