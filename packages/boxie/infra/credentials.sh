#!/usr/bin/env bash
# Generate the complete credential set atomically; never repair a partial set.
set -euo pipefail
umask 077
[[ $# == 1 && ! -L $1 ]] || { echo 'Use a private credentials directory.' >&2; exit 1; }
directory=$1
files=(installation-id host-id consumer.secret supervisor.secret postgres-password boxie-password temporal-password visibility-password grafana-admin postgres-dsn.secret)
if [[ -d $directory && -n $(ls -A "$directory") ]]; then
    for name in "${files[@]}"; do
        [[ -f $directory/$name && ! -L $directory/$name && -s $directory/$name ]] || {
            echo 'Partial credential set; restore the complete original set before retrying.' >&2; exit 1;
        }
        python3 -c 'import os,sys; sys.exit(bool(os.stat(sys.argv[1]).st_mode & 0o077))' "$directory/$name" || {
            echo 'Credentials must be private.' >&2; exit 1;
        }
    done
    exit 0
fi
install -d -m 0700 "$(dirname "$directory")"
staging=$(mktemp -d "${directory}.new.XXXXXX")
trap 'rm -rf "$staging"' EXIT
for name in consumer.secret supervisor.secret postgres-password boxie-password temporal-password visibility-password grafana-admin; do
    openssl rand -hex 32 > "$staging/$name"
done
python3 -c 'from uuid import uuid4; print(uuid4())' > "$staging/installation-id"
python3 -c 'from uuid import uuid4; print(uuid4())' > "$staging/host-id"
printf 'host=postgres dbname=boxie user=boxie password=%s sslmode=disable\n' "$(cat "$staging/boxie-password")" > "$staging/postgres-dsn.secret"
[[ ! -d $directory ]] || rmdir "$directory"
mv "$staging" "$directory"
