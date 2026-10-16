"""Separate journal records from exact owned-file warnings without inventing absence."""

import json
from pathlib import Path

import pytest
from observability_infra.smoke_runtime import Commands, Deadline


def reader(tmp_path: Path, stdout: str, stderr: str, code: int) -> list[str]:
    executable = tmp_path / "journalctl"
    executable.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"sys.stderr.write({stderr!r})\nsys.stdout.write({stdout!r})\nsys.exit({code})\n"
    )
    executable.chmod(0o700)
    return [str(executable), "--directory=" + str(tmp_path), "--output=cat", "--grep=probe"]


@pytest.mark.parametrize("code,stdout", [(0, '{"instance_id":"probe"}\n'), (1, "")])
@pytest.mark.parametrize(
    "warning,category", [("is truncated", "truncated_file"), ("corrupted", "corrupted_file")]
)
def test_owned_warning_is_partial_only_with_explicit_opt_in(
    tmp_path: Path, code: int, stdout: str, warning: str, category: str
) -> None:
    warning = f"Journal file {tmp_path}/system.journal {warning}, ignoring file.\n"
    args = reader(tmp_path, stdout, warning, code)
    commands = Commands(Deadline(3, reserve=0.1))
    with pytest.raises(RuntimeError):
        commands.read_journal(args, directory=tmp_path)
    result = commands.read_journal(args, directory=tmp_path, allow_incomplete=True)
    assert result.text == stdout.strip()
    assert result.observation["complete"] is False
    assert result.observation["warnings"] == {category: 1}
    assert str(tmp_path) not in json.dumps(result.observation)


@pytest.mark.parametrize(
    "warning,code",
    [
        ("Journal file /foreign/system.journal is truncated, ignoring file.\n", 0),
        ("PRIVATE diagnostic\n", 0),
        ("Failed to iterate through journal: Bad message\n", 1),
        ("Journal file {root}/system.journal uses an unsupported feature, ignoring file.\n", 0),
        ("Journal file {root}/system.journal corrupted, ignoring file.\n", 2),
        ("Journal file {root}/system.journal is truncated, ignoring file.", 0),
    ],
)
def test_unknown_foreign_and_real_errors_stay_fatal(
    tmp_path: Path, warning: str, code: int
) -> None:
    commands = Commands(Deadline(3, reserve=0.1))
    args = reader(tmp_path, "", warning.replace("{root}", str(tmp_path)), code)
    with pytest.raises(RuntimeError):
        commands.read_journal(args, directory=tmp_path, allow_incomplete=True)
    assert "PRIVATE" not in json.dumps(commands.last_failure)
    assert str(tmp_path) not in json.dumps(commands.last_failure)


def test_successful_records_and_empty_no_match_are_complete(tmp_path: Path) -> None:
    commands = Commands(Deadline(3, reserve=0.1))
    for output, code in [("{}\n", 0), ("", 1)]:
        result = commands.read_journal(reader(tmp_path, output, "", code), directory=tmp_path)
        assert result.observation["complete"] is True
        assert result.text == output.strip()


def test_separate_stderr_is_still_bounded(tmp_path: Path) -> None:
    commands = Commands(Deadline(3, reserve=0.1))
    args = reader(tmp_path, "", "PRIVATE" * 3000, 0)
    with pytest.raises(RuntimeError, match="limit"):
        commands.read_journal(args, directory=tmp_path, allow_incomplete=True)


def test_unfinished_stdout_stays_fatal(tmp_path: Path) -> None:
    commands = Commands(Deadline(3, reserve=0.1))
    with pytest.raises(RuntimeError, match="stream incomplete"):
        commands.read_journal(reader(tmp_path, "{}", "", 0), directory=tmp_path)
