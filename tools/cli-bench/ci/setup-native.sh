#!/usr/bin/env bash
# Explicit installation belongs to CI, never to the benchmark harness.
set -euo pipefail
mkdir -p "$BENCH_ROOT/logs"
if [[ "$RUNNER_OS" == Linux ]]; then
  sudo apt-get update
  sudo apt-get install --yes coreutils time
  tail_path=/usr/bin/tail
  mkdir_path=/usr/bin/mkdir
  /usr/bin/time --version > "$BENCH_ROOT/logs/time-version.txt" 2>&1
else
  brew install coreutils
  tail_path="$(brew --prefix coreutils)/bin/gtail"
  mkdir_path="$(brew --prefix coreutils)/bin/gmkdir"
  sw_vers > "$BENCH_ROOT/logs/time-version.txt"
  printf '\nNative /usr/bin/time -l (version unavailable)\n' >> "$BENCH_ROOT/logs/time-version.txt"
fi
"$tail_path" --version > "$BENCH_ROOT/logs/gnu-tail-version.txt"
"$mkdir_path" --version > "$BENCH_ROOT/logs/gnu-mkdir-version.txt"
hyperfine --version > "$BENCH_ROOT/logs/hyperfine-version.txt"
TAIL_PATH="$tail_path" MKDIR_PATH="$mkdir_path" uv run --frozen --offline --project "$BENCH_CI" python - <<'PY'
import os, shutil
from pathlib import Path
with open(os.environ['GITHUB_ENV'], 'a') as output:
    for key, value in [('GNU_TAIL', os.environ['TAIL_PATH']), ('GNU_MKDIR', os.environ['MKDIR_PATH']), ('HYPERFINE', shutil.which('hyperfine'))]:
        output.write(f'{key}={Path(value).resolve(strict=True)}\n')
PY
