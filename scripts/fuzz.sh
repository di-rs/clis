#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in parsu_xml|tail_bytes) target="$1" ;; *) echo 'Expected parsu_xml or tail_bytes' >&2; exit 2 ;; esac
seconds="${2:-15}"
[[ "$seconds" =~ ^[0-9]+$ ]] && (( seconds > 0 && seconds <= 600 )) || { echo 'Expected 1..600 seconds' >&2; exit 2; }
# cargo-fuzz 0.13.2 has no --locked option. Its Cargo build/metadata children use
# this narrow shim; a post-run comparison also detects any graph mutation.
export CLIS_REAL_CARGO
CLIS_REAL_CARGO=$(command -v cargo)
temporary=$(mktemp -d "${TMPDIR:-/tmp}/clis-fuzz.XXXXXX")
trap 'rm -rf "$temporary"' EXIT
cp scripts/locked-cargo.sh "$temporary/cargo"
chmod +x "$temporary/cargo"
cp fuzz/Cargo.lock "$temporary/fuzz.lock"
cp Cargo.lock "$temporary/workspace.lock"
export PATH="$temporary:$PATH"
# A portable cargo-fuzz binary may have been compiled for musl. Fuzz the host
# rustc target explicitly so AddressSanitizer uses the supported native libc.
host_target=$(rustc -vV | sed -n 's/^host: //p')
[[ -n "$host_target" ]] || { echo 'rustc did not report a host target' >&2; exit 1; }
cargo fetch --locked --manifest-path fuzz/Cargo.toml
cargo fuzz build "$target" --target "$host_target"
mkdir -p "fuzz/runs/$target/corpus" "fuzz/artifacts/$target"
cp -R "fuzz/corpus/$target/." "fuzz/runs/$target/corpus/"
cargo fuzz run "$target" "fuzz/corpus/$target" --target "$host_target" -- -runs=0 -max_len=4096 -timeout=5 -rss_limit_mb=1024
cargo fuzz run "$target" "fuzz/runs/$target/corpus" --target "$host_target" -- -max_total_time="$seconds" -max_len=4096 -timeout=5 -rss_limit_mb=1024
cmp "$temporary/fuzz.lock" fuzz/Cargo.lock
cmp "$temporary/workspace.lock" Cargo.lock
