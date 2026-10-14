"""Shared transport behavior used by both trusted publishers."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from ci.github import GitHub, PaginationLimit


@pytest.mark.parametrize(
    "mode", ["no-output", "stdout-closed", "trickle", "oversized", "exact-limit"]
)
def test_bounded_transport_kills_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    pid = tmp_path / "pid"
    command = tmp_path / "gh"
    command.write_text(
        f"#!{sys.executable}\nimport os, time\n"
        f"open({str(pid)!r}, 'w').write(str(os.getpid()))\n"
        f"mode = {mode!r}\n"
        "if mode == 'stdout-closed': os.close(1)\n"
        "if mode == 'oversized': os.write(1, b'x' * 17)\n"
        "if mode == 'exact-limit': os.write(1, b'x' * 16)\n"
        "if mode == 'trickle':\n"
        "    for _ in range(20):\n        os.write(1, b'x')\n        time.sleep(0.1)\n"
        "elif mode != 'exact-limit': time.sleep(2)\n"
    )
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    api = GitHub(timeout=0.4)
    started = time.monotonic()
    if mode == "exact-limit":
        assert api.request("user", limit=16) == b"x" * 16
    else:
        with pytest.raises(ValueError if mode == "oversized" else subprocess.TimeoutExpired):
            api.request("user", limit=16)
    assert time.monotonic() - started < 1.5
    with pytest.raises(ChildProcessError):
        os.waitpid(int(pid.read_text()), os.WNOHANG)


def test_pagination_preserves_filters_and_bounds_full_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = GitHub(max_pages=2)
    paths: list[str] = []

    def request(path: str, *, method: str = "GET", body: object = None) -> bytes:
        assert method == "GET" and body is None
        paths.append(path)
        return json.dumps([{"id": n} for n in range(100)]).encode()

    monkeypatch.setattr(api, "request", request)
    with pytest.raises(PaginationLimit):
        api.pages("repos/owner/name/deployments?environment=cli-playground%2Fpr-7")
    assert paths == [
        f"repos/owner/name/deployments?environment=cli-playground%2Fpr-7&per_page=100&page={page}"
        for page in (1, 2)
    ]
