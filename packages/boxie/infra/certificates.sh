#!/usr/bin/env bash
# All signing material stays in ca/. Runtime mounts expose only leaf identities.
set -euo pipefail
umask 077
[[ $# == 1 || $# == 3 ]] || exit 2
root=$1
[[ ! -L $root ]] || exit 1
if [[ -d $root ]]; then
    [[ -f $root/complete ]] || { echo 'Partial certificates; restore the complete set.' >&2; exit 1; }
    for name in server/frontend server/internode api/boxie-api supervisor/boxie-supervisor ui/ui-client operator/operator; do
        openssl x509 -checkend 604800 -noout -in "$root/$name.pem" >/dev/null || { echo 'Certificate renewal required.' >&2; exit 1; }
        [[ -s $root/$name.key ]] || exit 1
    done
    exit 0
fi
install -d -m 0700 "$(dirname "$root")"
staging=$(mktemp -d "${root}.new.XXXXXX")
trap 'rm -rf "$staging"' EXIT
install -d -m 0700 "$staging/ca"
for directory in server api supervisor ui operator; do install -d -m 0755 "$staging/$directory"; done
for name in frontend internode client; do
    openssl req -x509 -newkey rsa:3072 -nodes -days 365 -subj "/CN=Boxie $name CA" \
        -keyout "$staging/ca/$name.key" -out "$staging/ca/$name.pem" >/dev/null 2>&1
done
while read -r directory name issuer san usage; do
    openssl req -newkey rsa:2048 -nodes -subj "/CN=$name" -keyout "$staging/$directory/$name.key" -out "$staging/ca/$name.csr" >/dev/null 2>&1
    printf 'subjectAltName=DNS:%s\nextendedKeyUsage=%s\nbasicConstraints=CA:FALSE\n' "$san" "$usage" > "$staging/ca/$name.ext"
    openssl x509 -req -in "$staging/ca/$name.csr" -CA "$staging/ca/$issuer.pem" -CAkey "$staging/ca/$issuer.key" \
        -CAcreateserial -days 90 -extfile "$staging/ca/$name.ext" -out "$staging/$directory/$name.pem" >/dev/null 2>&1
    uid=0; gid=0
    case $directory in server|ui|operator) uid=1000; gid=1000 ;; esac
    chown "${2:-$uid}:${3:-$gid}" "$staging/$directory/$name.key"
done <<'EOF'
server frontend frontend temporal serverAuth
server internode internode temporal-internode serverAuth,clientAuth
api boxie-api client boxie-api clientAuth
supervisor boxie-supervisor client boxie-supervisor clientAuth
ui ui-client client ui clientAuth
operator operator client operator clientAuth
EOF
for directory in server api supervisor ui operator; do
    cp "$staging/ca/frontend.pem" "$staging/$directory/frontend-ca.pem"
done
for name in internode client; do cp "$staging/ca/$name.pem" "$staging/server/$name-ca.pem"; done
find "$staging" -name '*.pem' -exec chmod 0644 {} +
# The enclosing directory is traversable for the numeric readers; CA directory is private.
chmod 0755 "$staging"
touch "$staging/complete"
mv "$staging" "$root"
