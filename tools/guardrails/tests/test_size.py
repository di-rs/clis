"""Exercise real Git line counts without changing the caller repository."""

import os
import subprocess
import sys
from pathlib import Path


def test_size_excludes_lockfile(tmp_path: Path) -> None:
    local = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines()
    env = {key: val for key, val in os.environ.items() if key not in local}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        PYTHONPATH=str(Path(__file__).resolve().parents[2]),
    )

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, env=env, text=True)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Guardrail test")
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").strip()
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "uv.lock").write_text("ignored\n" * 300)
    git("add", ".")
    git("commit", "-qm", "head")
    result = subprocess.run(
        [sys.executable, "-m", "guardrails.pr_size", "--base", base],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "**2 / 200**" in result.stdout
