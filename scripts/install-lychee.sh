#!/usr/bin/env bash
# Pinned install-action predates this lychee release; verify the upstream binary.
set -euo pipefail
[[ "$(uname -sm)" == 'Linux x86_64' ]] || { echo 'This installer targets Linux x86_64.' >&2; exit 1; }
: "${RUNNER_TEMP:?GitHub runner temp directory is required}"
: "${GITHUB_PATH:?GitHub path file is required}"
curl --fail --silent --show-error --location --max-time 120 \
  https://github.com/lycheeverse/lychee/releases/download/lychee-v0.24.2/lychee-x86_64-unknown-linux-musl.tar.gz \
  --output "$RUNNER_TEMP/lychee.tar.gz"
echo "73657a111819a30c47c08352896796f23d64e4eb2b3ed39b6d32149241566fc5  $RUNNER_TEMP/lychee.tar.gz" | sha256sum --check
mkdir -p "$RUNNER_TEMP/lychee-bin"
tar -xzf "$RUNNER_TEMP/lychee.tar.gz" -C "$RUNNER_TEMP/lychee-bin" lychee
echo "$RUNNER_TEMP/lychee-bin" >> "$GITHUB_PATH"
"$RUNNER_TEMP/lychee-bin/lychee" --version
