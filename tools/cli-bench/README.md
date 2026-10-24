# cli-bench

cli-bench measures CLI performance from package-owned TOML suites. It generates
inputs with [Biggie](../../packages/biggie/README.md), checks each case before
timing it, and saves hyperfine reports for the candidate and an optional system
reference or previous build.

## Run a benchmark

Run it from the repository root with Python 3.15 or newer through the uv
workspace. Benchmarks require hyperfine 2.x and an existing candidate executable;
suites with `generate` also require Biggie. The default executable paths are
under `target/release/` in the workspace found above the suite. `--root` overrides
that workspace; if none is found, defaults are relative to the caller's directory.
Explicit relative paths are always resolved from the caller's directory.

```sh
cargo build --release -p tailr -p biggie
uv run cli-bench buildins/tailr/benches/cli-bench.toml
```

The runner prints progress and the report directory. Use `--list` to inspect
the suite's cases without running the benchmark.

## Arguments and options

| Argument or option | Meaning | Default |
| --- | --- | --- |
| `suite` | Path to a TOML suite | Required |
| `--candidate PATH` | Executable to measure | `target/release/<binary>` |
| `--baseline PATH` | Previous executable, using the candidate's arguments | None |
| `--reference PATH` | Override the suite's reference executable | Suite's `reference`, if set |
| `--biggie PATH` | Input generator executable | `target/release/biggie` |
| `--hyperfine PATH` | hyperfine 2.x executable | `hyperfine` on `PATH` |
| `--case NAME` | Select a case; repeat to select several | All cases |
| `--warmup N` | Warmup runs; zero is allowed | `3` |
| `--runs N` | Measured runs; must be positive | `20` |
| `--timeout SECONDS` | Positive timeout per generator, hook, preflight command, or complete hyperfine case | `300` |
| `--output DIR` | Report directory; must not already exist | `target/benchmarks/<binary>-<timestamp>` |
| `--root PATH` | Workspace for default executable/report paths | Discover above suite, then cwd |
| `--json` | Structured results; progress moves to stderr | Off |
| `--doctor` | Check a suite's dependencies without executing its cases | Off |
| `--version` | Show runner version | |
| `--list` | Print selected case names without running tools | Off |
| `-h`, `--help` | Show help | |

`--baseline` and `--reference` are mutually exclusive. A baseline overrides the
suite's reference and uses `args`; a reference uses `reference_args` when supplied.
Without either a reference or baseline, only the candidate is measured.

## Suite format

Keep suites in the CLI's `benches/` directory, with `binary` matching its package
and executable name. For example:

```toml
binary = "tailr"
reference = "tail"
generate = [["text", "--seed", "42", "--lines", "100000", "input.txt"]]

[[cases]]
name = "last-lines"
args = ["-n", "1000", "input.txt"]
```

| Suite field | Meaning |
| --- | --- |
| `binary` | Required executable name, without a directory path |
| `reference` | Optional comparison executable name or path |
| `generate` | Biggie argument arrays, run once before the cases |
| `env` | Environment variable names and string values |
| `cases` | Required, nonempty list of cases |

| Case field | Meaning |
| --- | --- |
| `name` | Required filename-safe identifier; unique ignoring case; `metadata` is reserved |
| `args` | Required argument array for the candidate |
| `reference_args` | Alternate argument array for a reference |
| `stdin` | Input file relative to the temporary working directory; absolute paths and `..` are rejected |
| `prepare` | Shell command before each preflight command and each timed run |
| `check` | Shell command after each preflight command, for checking side effects |
| `expected_exit` | Expected exit status, from 0 to 255; defaults to `0` |

### Input preparation and environment

Unknown fields are rejected. Generation and cases share a temporary working
directory, so a case that modifies inputs should restore them in `prepare`.
Hooks execute shell commands: run only trusted suites. `stdin` benchmarks read
from a regular file, so they do not measure a producer feeding a pipe.

The runner inherits the environment, sets `LC_ALL=C` and `TZ=UTC`, and disables
Sanctuary logging with `SANCTUARY_LOG_LEVEL=off` and `SANCTUARY_LOG_STDERR=off`.
Suite `env` values override these defaults.

## Results and failures

Before timing, the runner verifies exit statuses and compares candidate and
comparison stdout byte for byte. It does not compare stderr. A `check` hook runs
only during this preflight, outside timing. Measured exit statuses are checked
again in the hyperfine report.

### Read reports

Each completed case produces `<name>.json` and `<name>.md`. The output directory
also contains `suite.toml` and `metadata.json`, which records settings, executable
paths, completed cases, and overall status. If a run fails, inspect its error and
metadata, then rerun with a new output directory. Temporary inputs are removed.

Progress and the results path go to stdout; runner errors go to stderr. Exit
status is `0` for success, `1` for validation or execution failure, `2` for invalid
command-line syntax, and `130` for interruption. A one-sample run is a smoke
check, not evidence of a performance improvement.

## Pull request reports

CI maintains two separate comments: benchmark smoke results and Linux binary
sizes. Both link to the run and its artifacts. Draft PRs wait until they are ready
for review or carry the `run-ci` label.

### Benchmark smoke results

Benchmarks run on Ubuntu 24.04 and macOS 15 when a CLI's `src/**/*.rs` or
`build.rs` changes relative to the merge base. Each command receives one measured
run and no warmup. Manifest-only, shared-library-only, and PR title or body edits
do not select benchmark suites.

The comment starts with completed measurement counts, warnings, and a summary
by package and platform. Expand a package for case timings and comparison ratios;
expand **CI time and diagnostics** for job, build, and benchmark durations.
Comparisons identify the system tool or supplied previous executable. They are
not automatic PR-base comparisons, and one sample does not establish a performance
regression or improvement.

### Binary sizes

The size report covers Cargo binary packages throughout the workspace, including
`buildins/` and `packages/`, whether or not they have benchmark suites. Source,
manifest, and build-script changes select the owning packages and their dependent
binaries. Lockfile changes select binaries whose dependency closures changed.
Selection considers both revisions, so removed binaries remain visible.

Root Cargo manifests, `.cargo/` configuration, Rust toolchain files, and changes
to size collection, reporting, shared selection code, or the size workflows select
all binaries. Documentation-only and test-only changes do not select builds. Size selection is
independent of benchmark selection.

On Ubuntu 24.04, the collector builds the merge base and PR head with the PR's
pinned Rust toolchain. It uses locked dependencies, the release profile, default
features, and separate output directories, building each package separately.
It measures executable file bytes from Cargo's reported output paths, with no
additional stripping or compression. These are build artifact sizes, not memory
usage or download sizes.

The comment shows a total for the affected set, an expanded **Changed binaries**
table, and collapsed unchanged results and build details. Changes include bytes
and percentages; a zero or absent baseline has no percentage. Added and removed
binaries have explicit statuses. Failed or missing builds are unavailable, never
zero; incomplete results suppress the total. Size growth is advisory, while build
failures still fail the execution workflow.

The `cli-size-linux` artifact contains versioned `sizes.json` measurements for
14 days. Comments show at most 120 binaries, with changed binaries first; totals
use the entire affected set. Inspect the linked run logs for failed builds and
rerun the execution workflow after fixing their cause.

To collect locally from trusted repository revisions, run from the root:

```sh
uv run python tools/cli-bench/collect_sizes.py \
  --base origin/master --head HEAD --output target/sizes.json
```

This command creates temporary detached worktrees, builds both revisions using
the head's toolchain, writes JSON to `--output`, and removes the worktrees. It
requires Git, Cargo, rustup, and the pinned toolchain; rustup may download it.
`--root PATH` selects another repository and `--attempt N` records a CI attempt
(default `1`). Build diagnostics go to stderr. Exit status is `0` on success
(including no affected binaries), `1` for collection or build failure, and `2`
for invalid arguments. If building fails after selection, the output retains
unavailable statuses. Local output records the host target; PR publication
accepts only the Linux CI target.

### Publication boundary

PR builds run with read-only repository permissions. Separate reporting workflows
use default-branch code and treat bounded ZIP/JSON artifacts as data, without
executing or extracting them. Publishing validates the workflow, current PR head,
and artifact attempt, and updates only the publisher's own matching comment.
Older runs cannot overwrite a newer report. The new size reporting workflow must
be on the default branch before GitHub can publish its first completion report.

## Architecture

[The runner](src/cli_bench/command.py) validates suites, prepares a temporary working directory,
runs correctness checks, and invokes hyperfine. [Suite selection](select_suites.py)
decides which package-owned suites CI should run; [reporting](report.py) handles
GitHub results separately from execution. [Size collection](collect_sizes.py)
builds affected binaries; [size rendering](size_report.py) uses the same publication
checks. This keeps benchmark inputs and cases
with their CLI while sharing the measurement machinery.

## Installed use and JSON

From the repository root, `make install-python` installs cli-bench and cli-check
in isolated uv environments and places commands in `~/.local/bin`. Add that
directory to `PATH`. Python 3.15+ and uv are required for installation. The old
`uv run python tools/cli-bench/bench.py ...` entry point remains compatible.

```sh
cli-bench --json doctor
cli-bench --json /path/to/suite.toml --list
cli-bench --json /path/to/suite.toml --doctor --candidate /path/to/binary
cli-bench --json /path/to/suite.toml --candidate /path/to/binary --output ./report
```

`doctor` checks hyperfine 2.x; suite `--doctor` additionally checks candidate,
comparison, and generator availability as applicable. Only hyperfine's version
command executes. It creates no reports and runs no hooks or benchmark cases.
Results state `mode: "local"` and `auth: "not_required"`; a failed check exits 1.
The separate GitHub reporting script still uses `GH_TOKEN`/`GITHUB_TOKEN` and
requires explicit `--post` to update a comment.

JSON uses CLI envelopes, not external API pass-through. Listings return
`{"ok":true,"data":{"binary":"tailr","cases":[...]}}`. Doctor returns
`{"ok":true,"data":{"version":"0.1.0","mode":"local","auth":"not_required","root":"...","checks":[...]}}`;
each check includes `name`, `ok`, and either `path` or `message`. Failed checks
set the outer `ok` to false. Successful runs return
`{"ok":true,"data":{"output":"...","metadata":"...","completed_cases":["last-lines"]}}`.
Read the referenced files for measurements.

Errors return `{"ok":false,"error":{"kind":"operation","message":"..."}}`;
argument errors use `arguments`, interruption uses `interrupted`. Existing exit
codes are preserved. Help/version remain text. In JSON mode, runner progress and
inherited generator/hook/hyperfine stdout go to stderr so stdout contains one JSON
object. A failed run can leave its documented partial report directory.
