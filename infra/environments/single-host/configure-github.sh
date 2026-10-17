#!/usr/bin/env bash
# Run locally with the operator's gh authentication after setup verification.
set -euo pipefail
set +x
[[ $# == 4 ]] || { echo 'usage: configure-github.sh REPOSITORY HOST KNOWN_HOSTS_FILE SSH_KEY_FILE' >&2; exit 2; }
repository=$1 host=$2 known_hosts=$3 key=$4
[[ $repository =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ && $host =~ ^[a-zA-Z0-9.-]+$ ]]
[[ -s $known_hosts && -s $key ]]
ssh_options=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key")
private=$(mktemp -d)
umask 077
trap 'rm -rf "$private"' EXIT
ssh "${ssh_options[@]}" "root@$host" 'cat /etc/clis/boxie/preview-token' > "$private/token"
[[ $(cat "$private/token") =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid preview token; GitHub was not changed.' >&2; exit 1; }
url=$(ssh "${ssh_options[@]}" "root@$host" 'set -a; . /etc/clis/playground.env; printf "https://%s" "$PLAYGROUND_DOMAIN"')
[[ $url =~ ^https://[a-z0-9.-]+$ ]] || exit 1
for environment in boxie playground; do
    gh secret set DEPLOY_SSH_KEY --repo "$repository" --env "$environment" < "$key"
    gh secret set DEPLOY_KNOWN_HOSTS --repo "$repository" --env "$environment" < "$known_hosts"
    gh variable set DEPLOY_HOST --repo "$repository" --env "$environment" --body "$host"
done
gh secret set PLAYGROUND_DEPLOY_TOKEN --repo "$repository" --env playground-preview < "$private/token"
gh variable set PLAYGROUND_URL --repo "$repository" --env playground-preview --body "$url"
