#!/usr/bin/env bash
# The only runner-side SSH transport for the three fixed host operations.
set -euo pipefail
umask 077
[[ $# == 2 ]] || exit 2
app=$1 payload=$2
case $app in setup|boxie|playground) ;; *) exit 2 ;; esac
[[ ${DEPLOY_HOST:-} =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+$ ]]
[[ -n ${DEPLOY_SSH_KEY:-} && -n ${DEPLOY_KNOWN_HOSTS:-} ]]
private=$(mktemp -d)
trap 'rm -rf "$private"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$private/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$private/known_hosts"
cat > "$private/config" <<EOF
Host deployment
  HostName $DEPLOY_HOST
  User root
  IdentityFile $private/key
  IdentitiesOnly yes
  UserKnownHostsFile $private/known_hosts
  StrictHostKeyChecking yes
  BatchMode yes
  ConnectTimeout 15
  ServerAliveInterval 15
  ServerAliveCountMax 3
EOF

case $payload in
    --current)
        ssh -F "$private/config" deployment "cat /srv/clis/deployment/$app/current/revision"
        exit ;;
    --gateway-image|--sandbox-image|--boxie-revision)
        [[ $app == playground ]] || exit 2
        case $payload in
            --gateway-image) command='sed -n "s/^PLAYGROUND_IMAGE=//p" /srv/clis/deployment/playground/current/image.env' ;;
            --sandbox-image) command='sed -n "s/^PLAYGROUND_SANDBOX_IMAGE=//p" /srv/clis/deployment/playground/current/image.env' ;;
            --boxie-revision) command='cat /srv/clis/deployment/boxie/current/revision' ;;
        esac
        ssh -F "$private/config" deployment "$command"
        exit ;;
    --*) exit 2 ;;
esac

[[ -d $payload && ${GITHUB_RUN_ID:-} =~ ^[0-9]+$ && ${GITHUB_RUN_ATTEMPT:-} =~ ^[0-9]+$ ]]
[[ ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ ]]
run="$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
revision=$(cat "$payload/revision")
[[ $revision == "$GITHUB_SHA" ]]
if [[ $app != setup ]]; then
    expected=$(cat "$payload/expected-revision")
    [[ $expected == none || $expected =~ ^[a-f0-9]{40}$ ]]
fi
while IFS='=' read -r name image; do
    case $name in BOXIE_RUNTIME_IMAGE|PLAYGROUND_IMAGE|PLAYGROUND_SANDBOX_IMAGE|OBS_COLLECTOR_IMAGE) ;; *) exit 2 ;; esac
    [[ $image =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]]
done < "$payload/image.env"

ssh -F "$private/config" deployment "test ! -e /opt/clis/releases/$app/$run && install -d -m 0700 /opt/clis/staging/$app/$run /opt/clis/releases/$app"
scp -F "$private/config" -r "$payload" "deployment:/opt/clis/staging/$app/$run/payload"
ssh -F "$private/config" deployment "mv /opt/clis/staging/$app/$run/payload /opt/clis/releases/$app/$run"
unit="clis-$app@$run.service"
printf 'Operation: %s\n' "$unit" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
if ! ssh -F "$private/config" deployment "systemctl start $unit"; then
    echo "Operation outcome requires inspection: systemctl show $unit; journalctl -u $unit" >&2
    exit 1
fi
if ! ssh -F "$private/config" deployment "test \"\$(systemctl show $unit -p Result --value)\" = success && test \"\$(systemctl show $unit -p SubState --value)\" = exited && test \"\$(systemctl show $unit -p ExecMainStatus --value)\" = 0 && test \"\$(cat /srv/clis/deployment/$app/current/revision)\" = $GITHUB_SHA && test ! -e /srv/clis/deployment/pending"; then
    echo "Operation outcome requires inspection: systemctl show $unit; journalctl -u $unit" >&2
    exit 1
fi
