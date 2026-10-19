#!/bin/bash
set -eu
cp -R /opt/fixtures/. /workspace/
CLI_PLAYGROUND_CLIS=$(printf "%s\n" /opt/artifact/* | sed "s|.*/||" | tr "\n" " ")
export CLI_PLAYGROUND_CLIS
# This startup file is inside the disposable filesystem, never on the host.
cat > /workspace/.bashrc <<'RC'
export HOME=/workspace
export PATH=/opt/artifact:/usr/local/bin:/usr/bin:/bin
export PS1='\[\e[1m\]\w\[\e[0m\] $ '
export HISTFILE=/workspace/.bash_history
export SANCTUARY_LOG_LEVEL=off
export SANCTUARY_LOG_STDERR=off
# Otherwise Bash would silently execute its own true/false instead of Rust.
for cli in $CLI_PLAYGROUND_CLIS; do
    case "$cli" in true|false) enable -n "$cli" ;; esac
done
RC
printf '\r\n  %s / native Rust playground\r\n  Disposable files in /workspace. Type help for Bash help.\r\n\r\n' "$CLI_PLAYGROUND_CLIS"
# Boxie enforces absolute, inactivity, and disconnect deadlines independently.
exec /bin/bash --noprofile --rcfile /workspace/.bashrc -i
