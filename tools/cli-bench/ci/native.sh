#!/usr/bin/env bash
# Serial CLI orchestration only; Rust owns builds, validation and measurement.
set -euo pipefail
: "${BENCH_PACKAGES:?}" "${BENCH_ROOT:?}" "${BENCH_CLI:?}" "${CANDIDATE_SHA:?}" "${PREVIOUS_SHA:?}" "${RUSTUP_TOOLCHAIN:?}"
mkdir -p "$BENCH_ROOT/status" "$BENCH_ROOT/logs"
failed=0
selected=$(uv run --frozen --offline --project "$BENCH_CI" python - <<'SELECT'
import json, os, sys
sys.path.insert(0, os.environ['BENCH_CI'])
from impact import PACKAGES
packages = json.loads(os.environ['BENCH_PACKAGES'])
if not isinstance(packages, list) or len(packages) != len(set(packages)) or any(package not in PACKAGES for package in packages):
    raise ValueError('invalid selected packages')
print(' '.join(packages))
SELECT
)
# Empty selection performs no role or generator builds, even when called locally.
if [[ -z "$selected" ]]; then exit 0; fi
IFS=' ' read -r -a packages <<< "$selected"
# A checked 12 GiB logical workspace budget, plus a 2 GiB per-file hard limit.
# Check between commands; do not poll or run du concurrently with measurements.
ulimit -f 2097152
check_quota() {
  local used
  used=$(du -sk target "$BENCH_ROOT" | awk '{total += $1} END {print total}')
  [[ "$used" -le 12582912 ]]
}
record_status() {
  PACKAGE="$1" STAGE="$2" CODE="$3" uv run --frozen --offline --project "$BENCH_CI" python - <<'PY'
import json, os
from pathlib import Path
code = int(os.environ['CODE'])
value = None if code == 0 else {'stage': os.environ['STAGE'], 'message': 'CLI command or checked disk budget failed; see retained command log.', 'exit_code': code}
(Path(os.environ['BENCH_ROOT']) / 'status' / (os.environ['PACKAGE'] + '.json')).write_text(json.dumps(value) + '\n')
PY
}
case "$1" in
  build)
    for package in "${packages[@]}"; do
      code=0
      if ! check_quota; then record_status "$package" build 1; failed=1; continue; fi
      for revision in "$CANDIDATE_SHA" "$PREVIOUS_SHA"; do
        if "$BENCH_CLI" build -p "$package" -r "$revision" -t "$RUSTUP_TOOLCHAIN" -d "$BENCH_ROOT/data" >> "$BENCH_ROOT/logs/$package-build.log" 2>&1; then :; else code=$?; break; fi
      done
      if [[ "$code" == 0 ]]; then
        case "$package" in
          biggie) suite=biggie/benches/cli-bench.toml ;;
          *) suite="coreutils/$package/benches/cli-bench.toml" ;;
        esac
        generator=$(SUITE="$suite" uv run --frozen --offline --project "$BENCH_CI" python - <<'PY'
import os, tomllib
from pathlib import Path
from sys import path
path.insert(0, os.environ['BENCH_CI'])
from selection import sha
print(sha(tomllib.loads(Path(os.environ['SUITE']).read_text())['generator']['revision']))
PY
        )
        if "$BENCH_CLI" build -p biggie -r "$generator" -t "$RUSTUP_TOOLCHAIN" -d "$BENCH_ROOT/data" >> "$BENCH_ROOT/logs/$package-build.log" 2>&1; then :; else code=$?; fi
      fi
      record_status "$package" build "$code"
      if [[ "$code" != 0 ]]; then failed=1; fi
    done
    ;;
  run)
    : "${MEASUREMENT_PROFILE:?}" "${HYPERFINE:?}" "${GNU_TAIL:?}" "${GNU_MKDIR:?}"
    for package in "${packages[@]}"; do
      if [[ ! -f "$BENCH_ROOT/status/$package.json" ]] || [[ "$(cat "$BENCH_ROOT/status/$package.json")" != null ]]; then failed=1; continue; fi
      if ! check_quota; then record_status "$package" measurement 1; failed=1; continue; fi
      reference=()
      case "$package" in
        tailr) reference=(-x "$GNU_TAIL") ;;
        mkdirr) reference=(-x "$GNU_MKDIR") ;;
      esac
      code=0
      if "$BENCH_CLI" run -p "$package" -r "$CANDIDATE_SHA" -b "$PREVIOUS_SHA" \
          -t "$RUSTUP_TOOLCHAIN" -m "$MEASUREMENT_PROFILE" -H "$HYPERFINE" \
          -d "$BENCH_ROOT/data" -f markdown "${reference[@]}" > "$BENCH_ROOT/logs/$package-run.md" 2> "$BENCH_ROOT/logs/$package-run.stderr"; then :; else code=$?; fi
      record_status "$package" measurement "$code"
      if [[ "$code" != 0 ]]; then failed=1; fi
    done
    ;;
  *) exit 2 ;;
esac
exit "$failed"
