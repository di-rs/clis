# Run playground against a private Boxie service

## Independent local unit

Obtain a private Boxie URL and consumer credential from its operator; see
[Boxie infrastructure](../../../../packages/boxie/infra/README.md). Boxie owns its
default shell, sandbox image and built-in sample files. cli-playground uploads
validated native binaries and requests a sandbox using only their artifact ID.
It does not select an environment or modify Boxie's configuration.

```sh
docker build -f tools/cli-playground/infra/docker/CliPlayground.Dockerfile -t cli-playground:local .
```

From the repository root, create an external product-data volume and set the
consumer connection. The network must already exist; it can belong to a separate
Boxie installation. There is no cross-project `depends_on`.

```sh
docker volume create cli-playground-data
export CLI_PLAYGROUND_PROJECT_NAME=cli-playground
export CLI_PLAYGROUND_DATA_VOLUME=cli-playground-data
export CLI_PLAYGROUND_CONSUMER_NETWORK=boxie-consumers
export CLI_PLAYGROUND_DATABASE_NETWORK=boxie-postgres_playground
export CLI_PLAYGROUND_DATABASE_URL_FILE=/private/path/playground-database-url.secret
export CLI_PLAYGROUND_BOXIE_URL=http://boxie-api:8090
export CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE=/private/path/consumer.secret
export CLI_PLAYGROUND_DEPLOY_TOKEN=local-deploy-token
export CLI_PLAYGROUND_PUBLIC_URL=http://localhost:8000
docker compose -f tools/cli-playground/infra/compose.yml run --rm --no-deps backend \
  alembic upgrade head
docker compose -f tools/cli-playground/infra/compose.yml up --build -d
```

Both local and production manifests default to project `cli-playground`. Override
`CLI_PLAYGROUND_PROJECT_NAME` for another installation; keep it distinct from Boxie's
`BOXIE_PROJECT_NAME` (default `boxie`). Do not give the two units a shared
`COMPOSE_PROJECT_NAME` or `--project-name`, which overrides the manifest identity.
Their external network and volume identifiers remain independently configured.

The credential must be a regular non-symlink file, mode `0600`, containing a
16–4096-character printable token. The local unit publishes only localhost:8000.
The independently resolvable production manifest uses an explicit immutable
`CLI_PLAYGROUND_IMAGE`, external `CLI_PLAYGROUND_PROXY_NETWORK` and
`CLI_PLAYGROUND_CERTIFICATES_VOLUME`; only Traefik publishes 80/443 and receives the
Cloudflare certificate-renewal credential. Boxie's artifact data and supervisor
credential never enter cli-playground. Runtime images contain only their own installed
package/dependencies and, for cli-playground, its real built frontend assets.

Operator modules are installed separately from runtime images. Run
`uv sync --all-packages` for local infrastructure checks. Application
release jobs use native OpenSSH and do not read Pulumi state.
