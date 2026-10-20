"""The default shell seeds disposable files and resolves uploaded commands."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("uploaded_builtins", [False, True])
def test_default_shell_supports_files_and_uploaded_builtins(
    tmp_path: Path, uploaded_builtins: bool
) -> None:
    workspace = tmp_path / "workspace"
    artifact = tmp_path / "artifact"
    workspace.mkdir()
    artifact.mkdir()
    if uploaded_builtins:
        for name in ("true", "false"):
            executable = artifact / name
            executable.write_text(f"#!/bin/sh\nprintf 'uploaded-{name}\\n'\n")
            executable.chmod(0o755)
    script = (ROOT / "packages/boxie/sandbox/shell.sh").read_text()
    script = script.replace("/opt/fixtures", str(ROOT / "packages/boxie/sandbox/fixtures"))
    script = script.replace("/workspace", str(workspace)).replace("/opt/artifact", str(artifact))
    result = subprocess.run(
        ["bash", "-c", script],
        input="true; false; printf 'status:%s\\n' \"$?\"; exit 0\n",
        cwd=workspace,
        env={**os.environ, "TERM": "xterm-256color"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    if uploaded_builtins:
        assert result.stdout == "uploaded-true\nuploaded-false\nstatus:0\n"
    else:
        assert result.stdout == "status:1\n"
    for source in (ROOT / "packages/boxie/sandbox/fixtures").rglob("*"):
        if source.is_file():
            relative = source.relative_to(ROOT / "packages/boxie/sandbox/fixtures")
            assert (workspace / relative).read_bytes() == source.read_bytes()
