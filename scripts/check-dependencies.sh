#!/usr/bin/env bash
# Fresh advisories, license/source policy and unused-dependency checks.
set -euo pipefail
cd "$(dirname "$0")/.."
repo_root="$PWD"
audit_dir=$(mktemp -d "${TMPDIR:-/tmp}/clis-audit.XXXXXX")
trap 'rm -rf "$audit_dir"' EXIT
for manifest in Cargo.toml tests/consumers/Cargo.toml fuzz/Cargo.toml; do
  if [[ ! -f "$manifest" ]]; then
    continue
  fi
  lockfile="${manifest%Cargo.toml}Cargo.lock"
  cargo audit --db "$audit_dir/db" --file "$lockfile" --deny yanked
  cargo deny --locked --manifest-path "$manifest" --config "$repo_root/deny.toml" check licenses sources bans
done
cargo machete --with-metadata
