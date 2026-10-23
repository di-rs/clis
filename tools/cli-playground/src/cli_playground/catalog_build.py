"""Unprivileged build-time introspection; never imported by the publisher."""

import hashlib
import os
import re
import selectors
import signal
import subprocess
import tarfile
import tempfile
import time
import tomllib
from pathlib import Path

from cli_playground.catalog import CATALOG_NAME, MAX_CATALOG, Catalog, CliCatalog, CliSchema


def capture_schema(binary: Path, *, timeout: float = 10, limit: int = 1024 * 1024) -> CliSchema:
    """Bound bytes, elapsed time, and descendants, including inherited pipe handles."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}
    }
    environment.update(
        SANCTUARY_CLI_SCHEMA="1", SANCTUARY_LOG_LEVEL="off", NO_COLOR="1", LC_ALL="C", TERM="dumb"
    )
    with tempfile.TemporaryDirectory(prefix="cli-schema-") as cwd:
        with subprocess.Popen(
            [str(binary.resolve()), "--help"],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            assert process.stdout is not None
            data = bytearray()
            deadline = time.monotonic() + timeout
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ValueError("CLI metadata capture timed out")
                        if not selector.select(min(remaining, 0.1)):
                            continue
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > limit:
                            raise ValueError("CLI metadata exceeds output limit")
                if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                    raise ValueError("CLI metadata export failed")
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    return CliSchema.model_validate_json(bytes(data))


def build_catalog(root: Path, base: str, head: str, names: list[str], output: Path) -> None:
    base = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"], cwd=root, text=True
    ).strip()
    head = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{head}^{{commit}}"], cwd=root, text=True
    ).strip()
    if not all(re.fullmatch(r"[a-f0-9]{40}", ref) for ref in (base, head)):
        raise ValueError("catalog builds require commit SHAs")
    ancestor = subprocess.check_output(
        ["git", "merge-base", base, head], cwd=root, text=True
    ).strip()
    entries = []
    for name in names:
        binary = output / name
        with binary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append(
            CliCatalog(
                name=name,
                binary_sha256=digest,
                head=capture_schema(binary),
                base=None,
                comparison="unavailable",
            )
        )
    with tempfile.TemporaryDirectory(prefix="playground-base-") as directory:
        temporary = Path(directory)
        archive = temporary / "source.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(archive), ancestor], cwd=root, check=True
        )
        source = temporary / "source"
        source.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter="data")
        existing: set[str] = set()
        for folder in ("buildins", "packages"):
            for manifest in (source / folder).glob("*/Cargo.toml"):
                if (manifest.parent / "src/main.rs").is_file():
                    existing.add(tomllib.loads(manifest.read_text())["package"]["name"])
        for entry in entries:
            if entry.name not in existing:
                entry.comparison = "added"
        candidates = sorted(existing.intersection(names))
        if candidates and (source / "utils/cli-schema/Cargo.toml").is_file():
            try:
                subprocess.run(
                    [
                        "cargo",
                        "build",
                        "--locked",
                        "--release",
                        "--bins",
                        "--target-dir",
                        str(temporary / "target"),
                        *(arg for name in candidates for arg in ("--package", name)),
                    ],
                    cwd=source,
                    check=True,
                    timeout=600,
                )
            except subprocess.CalledProcessError, subprocess.TimeoutExpired:
                print("Merge-base compilation unavailable; publishing without comparison markers.")
            else:
                for entry in entries:
                    if entry.name not in candidates:
                        continue
                    try:
                        baseline = capture_schema(temporary / "target/release" / entry.name)
                        if baseline.command.name != entry.name:
                            raise ValueError("baseline CLI identity mismatch")
                        entry.base = baseline
                        entry.comparison = "available"
                    except ValueError, OSError, subprocess.TimeoutExpired:
                        entry.base = None
    catalog = Catalog(schema_version=1, head_sha=head, base_sha=ancestor, clis=entries)
    payload = catalog.canonical()
    if len(payload) > MAX_CATALOG:
        raise ValueError("catalog exceeds size limit")
    (output / CATALOG_NAME).write_bytes(payload)
