#!/bin/bash
set -eu
cp -R /opt/fixtures/. /workspace/
# This startup file is inside the disposable filesystem, never on the host.
cat > /workspace/.bashrc <<'RC'
export HOME=/workspace
export PATH=/opt/preview/bin:/usr/local/bin:/usr/bin:/bin
export PS1='\[\e[1m\]\w\[\e[0m\] $ '
export HISTFILE=/workspace/.bash_history
export CLIS_LOG_LEVEL=off
export CLIS_LOG_STDERR=off
# Otherwise Bash would silently execute its own true/false instead of Rust.
for cli in $PLAYGROUND_CLIS; do
    case "$cli" in true|false) enable -n "$cli" ;; esac
done
RC
printf '\r\n  %s / native Rust playground\r\n  Disposable files in /workspace. Type help for Bash help.\r\n\r\n' "$PLAYGROUND_CLIS"
# The lifetime remains bounded even if the gateway or its connection crashes.
exec /usr/bin/timeout --signal=KILL 900 /bin/bash --noprofile --rcfile /workspace/.bashrc -i
