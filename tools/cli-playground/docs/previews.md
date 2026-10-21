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

## Validation

Build the real frontend before `uv run pytest
packages/boxie/tests/test_packaging.py -m integration`. The required PR frontend job
checks isolated packaging and static responses. The separate
[shared-system lifecycle workflow](../../../.github/workflows/sanctuary-lifecycle.yml)
checks native runsc security and both generic and product terminals through the
private API. Missing runsc is a failure, never a successful runc substitute.
