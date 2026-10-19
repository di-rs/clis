#!/usr/bin/env bash
# Run from the checked-out repository root; credentials come from the protected job.
set -euo pipefail
umask 077
[[ $# == 1 ]] || exit 2
component=$1
case $component in
    boxie)
        owner=boxie
        files=packages/boxie/infra
        image_key=BOXIE_IMAGE
        retained_key=OBS_COLLECTOR_IMAGE
        ;;
    cli-playground-sandbox|cli-playground-backend)
        owner=cli-playground
        files=tools/cli-playground/infra
        if [[ $component == cli-playground-sandbox ]]; then
            image_key=CLI_PLAYGROUND_SANDBOX_IMAGE
            retained_key=CLI_PLAYGROUND_BACKEND_IMAGE
        else
            image_key=CLI_PLAYGROUND_BACKEND_IMAGE
            retained_key=CLI_PLAYGROUND_SANDBOX_IMAGE
        fi
        ;;
    *) echo 'Unknown release component' >&2; exit 2 ;;
esac
[[ $SOURCE_SHA =~ ^[a-f0-9]{40}$ && $BUILT_SHA == "$SOURCE_SHA" ]]
[[ $(git rev-parse HEAD) == "$SOURCE_SHA" ]]
[[ $IMAGE =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]]
[[ $DEPLOY_HOST =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+$ ]]
[[ $GITHUB_RUN_ID =~ ^[0-9]+$ && $GITHUB_RUN_ATTEMPT =~ ^[0-9]+$ ]]
private=$(mktemp -d)
remote="/run/sanctuary-$component-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
remote_created=false
cleanup() {
    status=$?
    if [[ $remote_created == true ]]; then
        ssh -F "$private/config" deployment "rm -rf $remote" || echo "Remote credential cleanup failed: $remote" >&2
    fi
    rm -rf "$private"
    exit "$status"
}
trap cleanup EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$private/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$private/known_hosts"
cat > "$private/config" <<SSH
Host deployment
  HostName $DEPLOY_HOST
  User root
  IdentityFile $private/key
  IdentitiesOnly yes
  UserKnownHostsFile $private/known_hosts
  StrictHostKeyChecking yes
  BatchMode yes
  ConnectTimeout 15
SSH
ssh -F "$private/config" deployment "install -d -m 0700 /opt/sanctuary/$owner $remote"
remote_created=true
scp -F "$private/config" -r "$files/." "deployment:/opt/sanctuary/$owner/"
retained=$(ssh -F "$private/config" deployment "sed -n 's/^$retained_key=//p' /etc/sanctuary/$owner-image.env")
[[ $retained =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]]
printf '%s=%s\n%s=%s\n' "$image_key" "$IMAGE" "$retained_key" "$retained" > "$private/image.env"
mkdir "$private/registry"
printf '{"auths":{"ghcr.io":{"auth":"%s"}}}\n' "$(printf '%s:%s' "$GITHUB_ACTOR" "$GHCR_TOKEN" | base64 | tr -d '\n')" > "$private/registry/config.json"
scp -F "$private/config" "$private/image.env" "deployment:$remote/image.env"
scp -F "$private/config" -r "$private/registry" "deployment:$remote/registry"
case $component in
    boxie) command="/opt/sanctuary/boxie/deploy.sh $SOURCE_SHA" ;;
    cli-playground-sandbox) command="/opt/sanctuary/cli-playground/sandbox-release.sh $IMAGE" ;;
    cli-playground-backend) command=/opt/sanctuary/cli-playground/deploy.sh ;;
esac
ssh -F "$private/config" deployment "set -e; trap 'rm -rf $remote' EXIT; install -m 0600 $remote/image.env /etc/sanctuary/$owner-image.env; DOCKER_CONFIG=$remote/registry bash $command"
