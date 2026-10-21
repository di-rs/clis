# Boxie

Boxie runs uploaded files in short-lived Linux sandboxes. Other services use its
private API to upload an archive, start a sandbox, attach a terminal, and stop it.
The browser playground is one consumer; Boxie can run independently.

## How it works

Boxie has two processes. The API authenticates consumers and records requests in
PostgreSQL. The supervisor runs containers, stores artifact files, and handles
terminal connections. Only the supervisor has Docker access.

Temporal workers inside the supervisor carry out provisioning, cleanup, and
recovery. PostgreSQL records ownership and capacity so retries do not create a
second sandbox or free a slot before deletion is confirmed.

## Run a deployment

Start with the [infrastructure guide](infra/README.md). You need PostgreSQL,
Temporal, private credentials, and a native Linux host with gVisor (`runsc`).
Docker Desktop can build images, but it cannot validate the required isolation.

After provisioning those dependencies and process configuration files, run from
the workspace root in separate terminals:

```sh
uv run --package boxie boxie-supervisor --config /etc/boxie/supervisor.json
uv run --package boxie boxie-api --config /etc/boxie/api.json
```

Run one supervisor per data directory. Keep both endpoints private and make the
artifact directory visible at the same absolute path to the supervisor and Docker.

## Use or change Boxie

| Task | Guide |
| --- | --- |
| Upload files, start sandboxes, or implement a terminal client | [Consumer API](docs/consumer-api.md) |
| Configure processes, credentials, or the default sandbox | [Configuration](docs/configuration.md) |
| Understand isolation, storage, cleanup, and recovery | [Execution](docs/execution.md) |
| Deploy or inspect logs | [Infrastructure](infra/README.md) and [monitoring](infra/observability/README.md) |

Consumers cannot choose arbitrary commands, environment settings, or isolation
profiles. The default sandbox provides Bash and sample files, has no network,
and limits CPU, memory, disk, and lifetime. A supervisor restart can end terminals.
See the execution guide for exact limits and recovery behavior.

Run focused tests with `uv run pytest packages/boxie/tests`.
Integration tests require their external dependencies; native isolation checks
run through the [lifecycle workflow](../../.github/workflows/boxie-lifecycle.yml).
Passing local tests does not establish production readiness.

[All projects](../../README.md)
