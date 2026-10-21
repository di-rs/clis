# Validate a Boxie deployment

## Validation

Default tests require no Docker or frontend build. Integration checks require
Docker Compose, uv and real built frontend assets; missing tools/assets fail.

```sh
uv run pytest packages/boxie/tests/test_packaging.py
pnpm --dir tools/cli-playground/frontend build
uv run pytest packages/boxie/tests/test_packaging.py -m integration
```

The optional native Linux [Boxie check](../../../.github/workflows/boxie-lifecycle.yml)
starts PostgreSQL, Temporal, API and Supervisor without cli-playground. It checks
process faults, the generic consumer API and container cleanup. The separate
[shared-system check](../../../.github/workflows/sanctuary-lifecycle.yml) adds the
application and browser to verify native CLIs, fixtures, pipes, resize and reconnect.
Both require real Linux amd64/runsc; image builds and fake-daemon tests do not
establish native isolation. Native tests remain manual while their hang is investigated.
PRs retain SQL/Temporal, credential permission and endpoint restart checks.

The lifecycle fixture runs API and supervisor as the runner's numeric UID:GID
so its private files and writable storage share one owner. Its Linux-volume DAC
regression resolves the actual test Compose mounts and checks readable private
credentials, immutable secret mounts, writable data/profiles and rejected unsafe
credentials. Run it with the freshly built Boxie image:

```sh
uv run pytest -m integration packages/boxie/tests/test_native_permissions.py
```

This probe can verify Linux ownership on an arm64 Docker host, but only the native
amd64/runsc lifecycle workflow proves the full lifecycle. The existing disposable
Temporal server/operator/UI fixture keys remain mode `0644` to support their
unchanged UID-1000 images and host-side test readers; this is a fixture limitation,
not the production provisioning policy. API/supervisor and host-only client fixture
keys are mode `0600`.

The native lifecycle fixture publishes the API on the test runner's loopback
interface. Its operator process runs `boxie_infra.smoke` there with Docker CLI
access to inspect sandbox security and deletion. Run the linked lifecycle
workflow with `workflow_dispatch` for this check. Production does not publish an
API host port, so the fixture's smoke command does not target a deployed host.
cli-playground never receives Docker access.
