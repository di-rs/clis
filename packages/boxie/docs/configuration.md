# Configure Boxie processes

## Run

Use the root workspace and lockfile:

```sh
uv sync --all-packages
uv run --package boxie boxie-supervisor --config /etc/boxie/supervisor.json
```

An operator configuration file contains:

```json
{
  "data_dir": "/var/lib/boxie",
  "postgres_dsn_file": "/run/secrets/postgres-dsn",
  "lifecycle": {
    "postgres_dsn_file": "/run/secrets/postgres-dsn",
    "temporal_address": "temporal:7233",
    "temporal_server_name": "temporal",
    "temporal_ca_file": "/run/secrets/temporal-ca.pem",
    "temporal_cert_file": "/run/secrets/temporal-client.pem",
    "temporal_key_file": "/run/secrets/temporal-client.key"
  },
  "profiles_file": "/etc/boxie/profiles.json",
  "credential_file": "/run/secrets/boxie-supervisor",
  "identity": {
    "installation_id": "b18283b1-dc32-46e7-8da5-7aa380043685",
    "host_id": "sandbox-1"
  },
  "bind_host": "127.0.0.1",
  "bind_port": 8091
}
```

Generate a unique installation UUID once and preserve it with the data directory.
The credential is a separate supervisor-control secret, at least 16 printable ASCII
bytes, in a private regular file (mode `0600`). Consumer API credentials must not
be reused here. Only trusted services on a private network may reach the supervisor;
there is no public endpoint. Access logging is disabled by the entry point and
credentials are never accepted in URLs.

Run the separate consumer API without a Docker socket or artifact host mounts:

```sh
uv run --package boxie boxie-api --config /etc/boxie/api.json
```

Its operator configuration contains:

```json
{
  "supervisor_url": "http://boxie-supervisor:8091",
  "supervisor_credential_file": "/run/secrets/boxie-supervisor",
  "consumer_credential_files": {
    "example-consumer": "/run/secrets/consumer"
  },
  "release": "boxie",
  "identity": {
    "installation_id": "b18283b1-dc32-46e7-8da5-7aa380043685",
    "host_id": "sandbox-1"
  },
  "lifecycle": {
    "postgres_dsn_file": "/run/secrets/postgres-dsn",
    "temporal_address": "temporal:7233",
    "temporal_server_name": "temporal",
    "temporal_ca_file": "/run/secrets/temporal-ca.pem",
    "temporal_cert_file": "/run/secrets/temporal-client.pem",
    "temporal_key_file": "/run/secrets/temporal-client.key"
  },
  "request_timeout": 20,
  "bind_host": "127.0.0.1",
  "bind_port": 8090
}
```

Each credential is a private regular file with the same restrictions as the supervisor
secret. Consumer credentials must be unique and distinct from the supervisor secret.
Only composition roots read secrets; transports receive injected credentials.
The API resolves consumer identity from Bearer authentication and never accepts
consumer identity in a sandbox request body. Neither API nor supervisor is public.
HTTP and WebSocket redirects are disabled for supervisor calls. HTTP control bodies
and responses are bounded to 64 KiB; uploads stream in chunks of at most 64 KiB
with an actual-byte ceiling of 512 MiB and a configured total request deadline.

## Default environment

Boxie owns one Ubuntu 24.04 Linux x86_64 environment with Bash, standard utilities
and built-in file fixtures. Uploads retain the existing flat ZIP format. Files are
mounted read-only at `/opt/artifact`, which is on PATH; built-in fixtures seed the
writable `/workspace`. Uploaded files never replace shell startup configuration.
The shell disables Bash's `true` and `false` builtins when uploaded executables
use those names. Workspace files remain non-executable.

The operator installs the Boxie sandbox image using `BOXIE_SANDBOX_IMAGE`. The
internal profile file is not a consumer interface. It contains one `default`:


```json
{
  "default": {
    "image_digest": "registry.example/boxie-sandbox@sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "commands": [["/opt/boxie/shell.sh"]],
    "env": {"TERM": ["xterm-256color"]},
    "artifact_target": "/opt/artifact"
  }
}
```

Replace the example image digest with the approved Boxie sandbox image. Local content-addressed
image IDs (`sha256:` plus 64 lowercase hex digits) are also accepted. Commands
are exact argument-vector allowlists; environment keys and values are allowlists.
The artifact mount must be a normalized path under `/opt/`. Optional `limits`
may lower the defaults but cannot increase them. Send `SIGHUP` to validate and
atomically reload all profiles. A failed reload keeps the last valid version.
Approved versions are retained by configuration hash, including across supervisor
restarts, so delayed requests can validate their original snapshot. Reloads never
change an admitted sandbox's image, command, environment, or limits.

Template selection and consumer-supplied templates require a separate design;
the current API exposes no template configuration.
