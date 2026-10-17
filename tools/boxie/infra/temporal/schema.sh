#!/bin/sh
set -eu
case "${1:-}" in
  setup|update) operation=$1 ;;
  *) echo 'usage: schema.sh setup|update (setup only for empty databases)' >&2; exit 2 ;;
esac
export SQL_PLUGIN=postgres12
case "${2:-all}" in
  temporal|visibility) stores=$2 ;;
  all) stores='temporal visibility' ;;
  *) echo 'unknown schema store' >&2; exit 2 ;;
esac
for store in $stores; do
  if [ "$store" = temporal ]; then
    export SQL_USER=temporal SQL_DATABASE=temporal SQL_PASSWORD="$TEMPORAL_DB_PASSWORD"
  else
    export SQL_USER=visibility SQL_DATABASE=temporal_visibility SQL_PASSWORD="$VISIBILITY_DB_PASSWORD"
  fi
  if [ "$operation" = setup ]; then
    temporal-sql-tool setup-schema -v 0.0
  fi
  temporal-sql-tool update-schema -d "/etc/temporal/schema/postgresql/v12/$store/versioned"
done
