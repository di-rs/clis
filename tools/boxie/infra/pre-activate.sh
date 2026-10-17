#!/bin/sh
# A Boxie release explicitly ends sessions; it promises no PTY handover.
set -eu
[ -n "${CLIS_PREVIOUS_RELEASE_DIR:-}" ] || exit 0
compose() {
  docker compose --env-file /dev/null --project-directory "$CLIS_RELEASE_DIR" \
    -p "$CLIS_COMPOSE_PROJECT" -f "$CLIS_COMPOSE_FILE" "$@"
}
# Stop admission before the orchestrator drains its embedded workers and terminals.
compose stop -t 10 api
compose stop -t 60 orchestrator
