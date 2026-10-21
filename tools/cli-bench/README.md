# cli-bench

cli-bench measures CLI performance from package-owned TOML suites. It generates
inputs with [Biggie](../../packages/biggie/README.md), checks each case before
timing it, and saves hyperfine reports for the candidate and an optional system
reference or previous build.

## Run a benchmark

Run it from the repository root with Python 3.15 or newer through the uv
workspace. Benchmarks require hyperfine 2.x and an existing candidate executable;
suites with `generate` also require Biggie. The default executable paths are
under `target/release/`.

```sh
cargo build --release -p tailr -p biggie
uv run python tools/cli-bench/bench.py buildins/tailr/benches/cli-bench.toml
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
