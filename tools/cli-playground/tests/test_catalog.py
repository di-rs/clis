"""Catalog admission never executes or trusts uploaded metadata."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from cli_playground.artifacts import (
    archive_catalog,
    binary_archive,
    canonical_archive,
    stage_archive,
)
from cli_playground.catalog import MAX_CATALOG, Catalog, parse_catalog
from cli_playground.catalog_build import capture_schema
from fastapi.testclient import TestClient

from .catalog_fixtures import catalog, schema
from .helpers import AUTH, binary_bundle, elf_binary, upload


def test_catalog_is_validated_and_stripped_from_executable_artifact(tmp_path: Path) -> None:
    expected = catalog()
    bundle = binary_bundle({"echor": elf_binary(), "catalog.json": expected.canonical()})
    assert binary_archive(bundle) == {"echor": elf_binary()}
    parsed = archive_catalog(bundle, "a" * 40, {"echor": hashlib.sha256(elf_binary()).hexdigest()})
    assert parsed == expected
    source = tmp_path / "upload.zip"
    source.write_bytes(bundle)
    names = stage_archive(source, tmp_path / "staged", 128)
    canonical_archive(tmp_path / "staged", names, tmp_path / "canonical.zip")
    assert archive_catalog((tmp_path / "canonical.zip"), "a" * 40, {}) is None
    assert (
        parsed is not None
        and parsed.digest() == Catalog.model_validate_json(parsed.canonical()).digest()
    )


@pytest.mark.parametrize(
    "invalid", ["version", "head", "digest", "name", "duplicates", "control", "extra", "comparison"]
)
def test_rejects_invalid_metadata(invalid: str) -> None:
    value = catalog().model_dump(by_alias=True)
    if invalid == "version":
        value["schema_version"] = 2
    elif invalid == "head":
        value["head_sha"] = "c" * 40
    elif invalid == "digest":
        value["clis"][0]["binary_sha256"] = "0" * 64
    elif invalid == "name":
        value["clis"][0]["head"]["command"]["name"] = "other"
    elif invalid == "duplicates":
        value["clis"].append(value["clis"][0])
    elif invalid == "control":
        value["clis"][0]["head"]["command"]["name"] = "a\nwhoami"
    elif invalid == "extra":
        value["instructions"] = "execute me"
    else:
        value["clis"][0]["comparison"] = "added"
    with pytest.raises(ValueError):
        parse_catalog(
            json.dumps(value).encode(),
            "a" * 40,
            {"echor": hashlib.sha256(elf_binary()).hexdigest()},
        )


def test_rejects_oversized_and_deep_catalogs() -> None:
    with pytest.raises(ValueError, match="size limit"):
        parse_catalog(b" " * (MAX_CATALOG + 1), "a" * 40, {})
    value = catalog().model_dump(by_alias=True)
    root = value["clis"][0]["head"]["command"]
    for _ in range(18):
        child = schema("nested").command.model_dump()
        root["subcommands"] = [child]
        root = child
    with pytest.raises(ValueError, match="tree exceeds"):
        Catalog.model_validate(value)


def test_catalog_route_is_revision_scoped_and_upload_identity_is_immutable(
    api_client: TestClient,
) -> None:
    original = catalog()
    saved = upload(api_client, clis={"echor": elf_binary(), "catalog.json": original.canonical()})
    url = f"/api/environments/{saved['id']}/revisions/{'a' * 40}/catalog"
    assert api_client.get(url).json() == original.model_dump(by_alias=True)
    upload(api_client, sha="c" * 40)
    assert api_client.get(url).json() == original.model_dump(by_alias=True)
    assert api_client.get(url.replace("a" * 40, "c" * 40)).json() is None
    assert api_client.get(url.replace("a" * 40, "d" * 40)).status_code == 404
    changed = catalog(about="Changed metadata, identical binary")
    response = api_client.post(
        "/api/environments?sha=" + "a" * 40 + "&pr=123",
        headers=AUTH,
        content=binary_bundle({"echor": elf_binary(), "catalog.json": changed.canonical()}),
    )
    assert response.status_code == 409
    assert api_client.get(url).json() == original.model_dump(by_alias=True)
    api_client.delete("/api/pull-requests/123", headers=AUTH)
    assert api_client.get(url).status_code == 404


def test_invalid_catalog_never_reaches_boxie(api_client: TestClient) -> None:
    value = catalog().model_dump(by_alias=True)
    value["head_sha"] = "c" * 40
    response = api_client.post(
        "/api/environments?sha=" + "a" * 40 + "&pr=123",
        headers=AUTH,
        content=binary_bundle({"echor": elf_binary(), "catalog.json": json.dumps(value).encode()}),
    )
    assert response.status_code == 422


def executable(path: Path, script: str) -> Path:
    path.write_text(f"#!{sys.executable}\n" + script)
    path.chmod(0o755)
    return path


def test_capture_uses_export_mode_with_isolated_cwd_and_no_ambient_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRIVATE_TOKEN", "must-not-inherit")
    payload = schema().model_dump_json(by_alias=True)
    binary = executable(
        tmp_path / "cli",
        f"import os,sys\nassert sys.argv[1:] == ['--help']\nassert os.environ['SANCTUARY_CLI_SCHEMA'] == '1'\nassert 'PRIVATE_TOKEN' not in os.environ\nassert os.getcwd() != {str(tmp_path)!r}\nprint({payload!r})\n",
    )
    assert capture_schema(binary) == schema()


@pytest.mark.parametrize(
    "script, message",
    [("import time\ntime.sleep(5)\n", "timed out"), ("print('x' * 8192)\n", "output limit")],
)
def test_capture_bounds_time_and_output(tmp_path: Path, script: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        capture_schema(executable(tmp_path / "cli", script), timeout=0.2, limit=128)


@pytest.mark.parametrize("baseline", ["supported", "legacy", "failed"])
def test_catalog_build_uses_merge_base_and_exact_head_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, baseline: str
) -> None:
    import os
    import subprocess

    from cli_playground.catalog_build import build_catalog

    from .helpers import clean_git_env, git_commit

    root = tmp_path / "repository"
    root.mkdir()
    env = clean_git_env()
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, env=env, text=True).strip()

    git("init", "-q")
    package = root / "buildins/echor"
    (package / "src").mkdir(parents=True)
    (package / "Cargo.toml").write_text('[package]\nname="echor"\n')
    (package / "src/main.rs").write_text("baseline marker")
    if baseline != "legacy":
        exporter = root / "utils/cli-schema"
        exporter.mkdir(parents=True)
        (exporter / "Cargo.toml").write_text('[package]\nname="cli-schema"\n')
    git("add", ".")
    git_commit(root)
    ancestor = git("rev-parse", "HEAD")
    # The base branch moves after the fork. It must not become the comparison.
    (package / "src/main.rs").write_text("base branch moved")
    added_on_base = root / "buildins/fresh"
    (added_on_base / "src").mkdir(parents=True)
    (added_on_base / "Cargo.toml").write_text('[package]\nname="fresh"\n')
    (added_on_base / "src/main.rs").write_text("fn main() {}")
    git("add", ".")
    git_commit(root)
    base = git("rev-parse", "HEAD")
    git("checkout", "--detach", ancestor)
    (package / "src/main.rs").write_text("head version")
    git("add", ".")
    git_commit(root)
    head = git("rev-parse", "HEAD")
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    for name in ("echor", "fresh"):
        executable(
            binaries / name,
            f"print({schema(name, 'Head interface').model_dump_json(by_alias=True)!r})\n",
        )
    commands = tmp_path / "commands"
    commands.mkdir()
    output = schema(about="Baseline interface").model_dump_json(by_alias=True)
    program = f"#!{sys.executable}\nprint({output!r})\n"
    executable(
        commands / "cargo",
        "import sys\nfrom pathlib import Path\n"
        "assert Path('buildins/echor/src/main.rs').read_text() == 'baseline marker'\n"
        + (
            "sys.exit(1)\n"
            if baseline == "failed"
            else "target=Path(sys.argv[sys.argv.index('--target-dir')+1])/'release'\ntarget.mkdir(parents=True)\n"
            f"binary=target/'echor'\nbinary.write_text({program!r})\nbinary.chmod(0o755)\n"
        ),
    )
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + env["PATH"])
    build_catalog(root, base, head, ["echor", "fresh"], binaries)
    hashes = {
        name: hashlib.sha256((binaries / name).read_bytes()).hexdigest()
        for name in ("echor", "fresh")
    }
    result = parse_catalog((binaries / "catalog.json").read_bytes(), head, hashes)
    assert result.base_sha == ancestor
    assert result.clis[0].head.command.about == "Head interface"
    assert result.clis[1].comparison == "added"
    if baseline == "supported":
        assert result.clis[0].comparison == "available"
        assert (
            result.clis[0].base is not None
            and result.clis[0].base.command.about == "Baseline interface"
        )
    else:
        assert result.clis[0].comparison == "unavailable"
        assert result.clis[0].base is None
