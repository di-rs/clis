# Shared host infrastructure

This package provisions the VM, protected persistent disk, pinned SSH identity,
Docker/gVisor bootstrap and explicit network/storage preparation. PostgreSQL and
all supporting services belong to [Boxie](../../tools/boxie/infra/README.md).
The [single-host environment](../environments/single-host/README.md) selects
owners and aggregates their numeric directory/volume declarations.

Copy `host_infra/resources.py` from the reviewed revision to the root-owned setup
directory and run `python3 resources.py --plan resources.json`. The exported
`sharedResources` plan requires the bootstrap-installed storage guard and mounted
persistent disk. Preparation validates ownership, bridge isolation and backing
paths before creating absent resources. Conflicts fail closed; it never adopts,
repairs, deletes, or activates workloads. Resource labels retain the legacy
`clis.owner=platform` storage identity to avoid re-adopting existing resources.

Directory readers are declared by service owners: PostgreSQL 999, Boxie/Playground
10001, Vector 65532, VictoriaLogs/Prometheus 65534, Grafana 472. Docker's persistent
data root and prestart storage guard prevent a missing disk from redirecting data
to the root filesystem. The administrative deployment key stays in the protected
environment secret store, separate from the public connection profile.

Host bootstrap accepts Boxie's bounded persistent journald policy. It installs
journal ACL tools, grants the systemd-journal group read/traversal and inherited
ACLs, and restarts/flushes journald with bounded commands. The public numeric GID
is in `/etc/boxie-platform/journal-group-gid`; never guess it from another host.
