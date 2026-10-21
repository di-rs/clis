# cli-check

`cli-check` checks the workspace's CLI package layout, tests, documentation, and
benchmark declarations. It reads repository files; it does not execute the CLIs
or prove that their behavior is correct.

## Check a package

Run from the repository root after `uv sync --all-packages`:

```sh
uv run cli-check -p echor
```

Repeat `-p` for more packages, or omit it to inspect the workspace. Success prints
`CLI checks passed`. Failures go to stderr with a path and rule identifier.
Exit codes are 0 for success, 1 for violations or invalid repository data, and 2
for invalid arguments.

## Select a check

| Command after `uv run` | What it checks |
| --- | --- |
| `cli-check layout -p echor` | One matching package, directory, and binary, using `src/main.rs` |
| `cli-check tests -p echor` | CLI integration test registration, helper dependency, and fixture layout |
| `cli-check docs -p echor` | Package README, root index entry, and local link targets |
| `cli-check benches -p echor` | Benchmark suite or explicit exemption |

| Argument or option | Meaning |
| --- | --- |
| `layout`, `tests`, `docs`, or `benches` | Optional check group; omit it to run all four. |
| `-p, --package NAME` | Select a Cargo package; repeat for more packages. Default: whole workspace. |
| `--root PATH` | Select another Cargo workspace. |
| `--json` | Emit structured results and errors on stdout. |
| `--list-packages` | Discover packages without running checks. |
| `--version` | Show version. |
| `-h, --help` | Print help. |

See the [check reference](docs/checks.md) for inventory limits and benchmark
exemptions. Markdown fragment validation belongs to the separate link checker.

## Develop the checker

```sh
uv run pytest tools/cli-check/tests
```

Tests exercise the real command in temporary workspaces. The normal commit hook
selects affected packages; run `cli-check` directly during development rather
than invoking the hook runner. Python CI checks the full workspace.

## Architecture

[Workspace discovery](src/cli_check/workspace.py) reads Cargo package metadata.
The [command](src/cli_check/command.py) selects packages and dispatches to independent
layout, test, documentation, and benchmark checks. Checks return diagnostics rather
than modifying packages. [Affected-package selection](src/cli_check/affected.py)
and [hook planning](src/cli_check/hooks.py) keep repository integration separate
from the rules themselves.

## Installed use and JSON

From the repository root, `make install-python` installs cli-check and cli-bench
in isolated uv environments, with commands in `~/.local/bin`. Add that directory
to `PATH`. Installation requires uv and Python 3.15+. Checking requires Cargo;
there is no authentication or service dependency.

```sh
cli-check --json --root /path/to/workspace --list-packages
cli-check --json --root /path/to/workspace tests -p echor
cli-check --json --root /path/to/workspace
```

Without `--root`, discovery starts at the current directory. JSON is a CLI
envelope: discovery returns `{"ok":true,"data":{"packages":[...]}}`, with each
package containing `name`, absolute `path`, and boolean `cli`. Check results use
`{"ok":true,"data":{"root":"...","packages":["echor"],"checks":["tests"],"diagnostics":[]}}`.
Violations set `ok: false`, exit 1, and populate diagnostics with stable `path`,
`group`, `rule`, and `message` fields in sorted order.

Operational errors return `{"ok":false,"error":{"kind":"operation","message":"..."}}`;
argument errors use kind `arguments` and exit 2. All JSON goes to stdout;
stderr stays available for diagnostics. Help/version remain text. Text mode
retains the existing success and diagnostic formatting.
