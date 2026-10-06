#!/usr/bin/env bash
set -euo pipefail
: "${CLIS_REAL_CARGO:?Real Cargo executable is required}"
case "${1:-}" in
  build|metadata) exec "$CLIS_REAL_CARGO" "$@" --locked ;;
  *) exec "$CLIS_REAL_CARGO" "$@" ;;
esac
