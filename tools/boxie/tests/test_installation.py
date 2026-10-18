"""Installation credentials are stable, private, and never silently repaired."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/boxie/infra/credentials.sh"


def credentials(directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), str(directory)], text=True, capture_output=True)


def test_generated_credentials_survive_rerun_without_output(tmp_path: Path) -> None:
    directory = tmp_path / "boxie"
    first = credentials(directory)
    assert first.returncode == 0, first.stderr
    before = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    assert len(before["consumer.secret"].strip()) >= 48
    assert before["consumer.secret"] != before["orchestrator.secret"]
    (tmp_path / "data").write_text("persistent sentinel")
    second = credentials(directory)
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout == ""
    assert before == {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }
    assert (tmp_path / "data").read_text() == "persistent sentinel"
    assert all(path.stat().st_mode & 0o077 == 0 for path in directory.iterdir() if path.is_file())


def test_partial_credentials_fail_without_changing_any_file(tmp_path: Path) -> None:
    directory = tmp_path / "boxie"
    directory.mkdir(mode=0o700)
    secret = directory / "consumer.secret"
    secret.write_text("existing-secret")
    secret.chmod(0o600)
    result = credentials(directory)
    assert result.returncode == 1
    assert "partial" in result.stderr.lower()
    assert secret.read_text() == "existing-secret"
    assert set(directory.iterdir()) == {secret}
    assert "existing-secret" not in result.stdout + result.stderr


def test_credentials_refuse_symlink_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    result = credentials(link)
    assert result.returncode == 1
    assert not list(target.iterdir())


@pytest.mark.parametrize("mode", [0o644, 0o640])
def test_credentials_reject_group_or_world_readable_files(tmp_path: Path, mode: int) -> None:
    directory = tmp_path / "boxie"
    assert credentials(directory).returncode == 0
    secret = directory / "consumer.secret"
    secret.chmod(mode)
    result = credentials(directory)
    assert result.returncode == 1
    assert "private" in result.stderr.lower()
    assert secret.stat().st_mode & 0o777 == mode


def test_failed_certificate_generation_leaves_no_final_directory(tmp_path: Path) -> None:
    root = tmp_path / "tls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    openssl = fake_bin / "openssl"
    openssl.write_text("#!/bin/sh\nexit 17\n")
    openssl.chmod(0o755)
    script = ROOT / "tools/boxie/infra/certificates.sh"
    failed = subprocess.run(
        ["bash", str(script), str(root), str(os.getuid()), str(os.getgid())],
        env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 17
    assert not root.exists()
    assert not list(tmp_path.glob("tls.new.*"))
    retry = subprocess.run(
        ["bash", str(script), str(root), str(os.getuid()), str(os.getgid())],
        capture_output=True,
        text=True,
    )
    assert retry.returncode == 0, retry.stderr
    assert (root / "complete").is_file()


def test_certificates_have_separate_identities_and_private_ca(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "tools/boxie/infra/certificates.sh"),
            str(tmp_path / "tls"),
            str(os.getuid()),
            str(os.getgid()),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    root = tmp_path / "tls"
    keys = [
        root / name / file
        for name, file in (
            ("server", "frontend.key"),
            ("api", "boxie-api.key"),
            ("orchestrator", "boxie-orchestrator.key"),
            ("ui", "ui-client.key"),
            ("operator", "operator.key"),
        )
    ]
    assert len({key.read_bytes() for key in keys}) == 5
    assert not any(path.name.endswith(".key") for path in (root / "server").glob("*ca*"))
    check = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            str(root / "server/client-ca.pem"),
            str(root / "api/boxie-api.pem"),
        ],
        capture_output=True,
    )
    assert check.returncode == 0
    assert all(key.stat().st_mode & 0o077 == 0 for key in keys)


def test_temporal_schema_setup_can_resume_one_database(tmp_path: Path) -> None:
    command = tmp_path / "temporal-sql-tool"
    command.write_text('#!/bin/sh\nprintf "%s:%s\\n" "$SQL_DATABASE" "$*" >> "$COMMAND_LOG"\n')
    command.chmod(0o755)
    log = tmp_path / "calls"
    result = subprocess.run(
        ["sh", str(ROOT / "tools/boxie/infra/temporal/schema.sh"), "setup", "visibility"],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "COMMAND_LOG": str(log),
            "TEMPORAL_DB_PASSWORD": "secret1",
            "VISIBILITY_DB_PASSWORD": "secret2",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "temporal_visibility:setup-schema -v 0.0",
        "temporal_visibility:update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned",
    ]
