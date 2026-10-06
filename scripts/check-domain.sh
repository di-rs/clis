#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Disallowed methods default to warning. A separate config leaves legitimate
# CLI/global initialization paths under the normal workspace lint policy.
export CLIPPY_CONF_DIR="$PWD/.config/domain-clippy"
cargo clippy --locked --no-deps -p biggie -p tailr --lib -- -D warnings -D clippy::disallowed_methods
