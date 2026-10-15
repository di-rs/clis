#!/bin/sh
set -eu
# Listing succeeds only after authenticated transport and cluster readiness.
# Do not interpret arbitrary describe/transport failures as NotFound.
temporal_cli() {
  temporal "$@" --address temporal:7233 --tls --tls-ca-path /certs/frontend-ca.pem \
    --tls-cert-path /certs/operator.pem --tls-key-path /certs/operator.key \
    --tls-server-name temporal --command-timeout 10s --color never
}
temporal_cli operator cluster health
listing=$(temporal_cli operator namespace list --output json)
exists=$(printf '%s' "$listing" | jq -r 'if type == "array" then any(.[]; .namespaceInfo.name == "boxie") else error("namespace list must be an array") end')
if [ "$exists" = false ]; then
  if created=$(temporal_cli operator namespace create --namespace boxie --retention 7d 2>&1); then
    printf '%s\n' "$created"
  else
    # Exact pinned CLI 1.9.0 rendering of the AlreadyExists RPC error. Every
    # other creation failure, including transport/auth failures, is fatal.
    case "$created" in
      'Error: unable to create namespace boxie: Namespace "boxie" already exists') ;;
      *) printf '%s\n' "$created" >&2; exit 1 ;;
    esac
  fi
fi
# CLI 1.9.0 list exhausts all service pages. Verify live post-create state, also
# after a concurrent bootstrap has already created the namespace.
temporal_cli operator namespace describe --namespace boxie --output json | \
  jq -e '.namespaceInfo.name == "boxie" and .namespaceInfo.state == "NAMESPACE_STATE_REGISTERED" and .config.workflowExecutionRetentionTtl == "604800s"' >/dev/null
