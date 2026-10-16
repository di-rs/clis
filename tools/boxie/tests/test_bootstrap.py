"""Pinned bootstrap failure classification, using an explicit CLI protocol peer."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize(
    "fault", ["health", "list", "auth", "parse", "create", "already", "wrong-retention"]
)
def test_namespace_bootstrap_failure_and_already_exists_race(tmp_path: Path, fault: str) -> None:
    assert shutil.which("jq"), "jq required for the committed namespace bootstrap script"
    peer = tmp_path / "temporal"
    peer.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *"cluster health"*) [ "$FAULT" != health ] ;;
  *"namespace list"*)
    case "$FAULT" in
      list|auth) echo 'transport or authentication failed' >&2; exit 1 ;;
      parse) echo 'not JSON' ;;
      *) echo '[]' ;;
    esac ;;
  *"namespace create"*)
    case "$FAULT" in
      already) echo 'Error: unable to create namespace boxie: Namespace "boxie" already exists' >&2; exit 1 ;;
      create) echo 'transport failed' >&2; exit 1 ;;
    esac ;;
  *"namespace describe"*)
    ttl=604800s
    [ "$FAULT" != wrong-retention ] || ttl=3600s
    printf '{"namespaceInfo":{"name":"boxie","state":"NAMESPACE_STATE_REGISTERED"},"config":{"workflowExecutionRetentionTtl":"%s"}}\\n' "$ttl" ;;
esac
""")
    peer.chmod(0o700)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["sh", str(ROOT / "infra/temporal/bootstrap.sh")],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "FAULT": fault,
            "CALLS": str(calls),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == (fault == "already"), result.stderr
    invoked = calls.read_text()
    if fault in {"health", "list", "auth", "parse"}:
        assert "namespace create" not in invoked
