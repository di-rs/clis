#!/bin/bash
set -eu
cp -R /opt/fixtures/. /workspace/
# This startup file is inside the disposable filesystem, never on the host.
cat > /workspace/.bashrc <<'RC'
export HOME=/workspace
export PATH=/opt/artifact:/usr/local/bin:/usr/bin:/bin
export PS1='\[\e[1m\]\w\[\e[0m\] $ '
export HISTFILE=/workspace/.bash_history
# Let uploaded executables override Bash builtins with the same names.
for cli in true false; do
    if [[ -x /opt/artifact/$cli ]]; then enable -n "$cli"; fi
done
RC
# Boxie enforces absolute, inactivity, and disconnect deadlines independently.
exec /bin/bash --noprofile --rcfile /workspace/.bashrc -i
