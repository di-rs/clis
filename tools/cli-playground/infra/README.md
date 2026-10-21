# Playground infrastructure

The playground deployment runs a Python backend with its built browser assets.
It connects to a separately operated Boxie service to create terminals. It never
receives the Docker socket or Boxie's supervisor credentials.

## Choose a setup

| Task | Guide |
| --- | --- |
| Set up or release on the shared production host | [Shared-host deployment](../../../infra/environments/single-host/README.md) |
| Run a local Compose deployment against an existing Boxie service | [Local setup](docs/local-setup.md) |
| Provision PostgreSQL, migrate, back up, or restore | [Database operations](docs/database.md) |
| Configure GitHub credentials and publish live PR links | [Preview publication](docs/previews.md) |

## Directory layout

| Path | Contents |
| --- | --- |
| `compose*.yml` | Local, production, and integration-test service definitions |
| `Pulumi*.yaml`, `__main__.py` | Pulumi project and stack entry points |
| `cli_playground_infra/` | Python provisioning and operator helpers |
| `docker/` | Application Dockerfile and its build-context exclusions |
| `scripts/` | Host bootstrap, installation, database provisioning, and releases |
| `postgres/` | Database provisioning SQL |
| `proxy/` | Traefik routing configuration |
| `observability/` | Log validation and collection configuration |
| `docs/` | Setup, database, and preview-publication guides |

Local setup requires a private Boxie URL and consumer credential, a dedicated
playground database, and their external Docker networks. Production additionally
uses Traefik for HTTPS. Only the proxy publishes ports 80 and 443.

## Keep ownership clear

The playground owns preview metadata and browser sessions in its PostgreSQL
database. Its data volume holds ZIPs waiting for upload. Boxie owns execution,
artifact storage, sandbox images, and sample files.

Use separate Compose project names for Boxie and the playground. The playground
defaults to `cli-playground`; do not override both projects with the same
`COMPOSE_PROJECT_NAME`. An application release updates the backend/frontend
image while leaving the proxy and Boxie running.

A green PR build does not prove a preview was published. Check the separate
publisher workflow, its environment approval, and the resulting browser link.
The preview guide explains missing configuration and retry behavior.
