"""Only application runtime inputs trigger a release."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from boxie_infra.release_selection import changed, runtime_requirements

ROOT = Path(__file__).resolve().parents[3]


def test_uv_export_excludes_dev_dependencies_and_fails_on_bad_lock(tmp_path: Path) -> None:
    for name in ("pyproject.toml", "uv.lock", ".python-version"):
        shutil.copy2(ROOT / name, tmp_path / name)
    for manifest in ROOT.glob("**/pyproject.toml"):
        if ".venv" in manifest.parts or manifest == ROOT / "pyproject.toml":
            continue
        target = tmp_path / manifest.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest, target)
    exported = runtime_requirements(tmp_path, "boxie")
    assert b"psycopg==" in exported
    assert b"pytest==" not in exported
    (tmp_path / "uv.lock").write_text("not a lock")
    with pytest.raises(subprocess.CalledProcessError):
        runtime_requirements(tmp_path, "boxie")


@pytest.mark.parametrize(
    "path",
    ["pyproject.toml", "tools/boxie/infra/compose.observability.yml"],
)
def test_boxie_selection_includes_root_build_settings_and_metrics_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repository), *args], env=environment, text=True
        ).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("before\n")
    git("add", ".")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    target.write_text("after\n")
    git("add", ".")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "candidate")
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(repository)
    assert changed("boxie", base) == (True, False)
