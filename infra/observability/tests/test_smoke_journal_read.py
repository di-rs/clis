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


@pytest.mark.parametrize(
    "line,reason,category",
    [
        (
            "Journal file /foreign/system.journal corrupted, ignoring file.\n",
            "foreign_parent",
            "corrupted_file",
        ),
        (
            "Journal file {root}//system.journal corrupted, ignoring file.\n",
            "path_spelling",
            "corrupted_file",
        ),
        (
            "Journal file {root}/PRIVATE corrupted, ignoring file.\n",
            "file_suffix",
            "corrupted_file",
        ),
        (
            "Journal file {root}/PRIVATE\x1b.journal corrupted, ignoring file.\n",
            "path_control",
            "corrupted_file",
        ),
        (
            "\x1b[31mJournal file {root}/system.journal corrupted, ignoring file.\x1b[0m\n",
            "format",
            "unknown",
        ),
        (
            "Journal file {root}/system.journal uses an unsupported feature, ignoring file.\n",
            "format",
            "unsupported_feature",
        ),
        (
            "Use SYSTEMD_LOG_LEVEL=debug journalctl --file={root}/system.journal to see the details.\n",
            "format",
            "unsupported_details",
        ),
        (
            "Too many journal files (limit is at 7168) in scope, ignoring file 'PRIVATE'.\n",
            "format",
            "too_many_files",
        ),
        (
            "An error was encountered while opening journal file or directory PRIVATE, ignoring file: Input/output error\n",
            "format",
            "open_error",
        ),
        (
            "No journal files were opened due to insufficient permissions.\n",
            "format",
            "access_denied",
        ),
        ("No journal files were found.\n", "format", "no_files"),
        ("PRIVATE\n", "format", "unknown"),
    ],
)
def test_rejection_classification_is_fixed_and_never_exports_line(
    tmp_path: Path, line: str, reason: str, category: str
) -> None:
    commands = Commands(Deadline(3, reserve=0.1))
    args = reader(tmp_path, "{}\n", line.replace("{root}", str(tmp_path)), 0)
    with pytest.raises(RuntimeError):
        commands.read_journal(args, directory=tmp_path, allow_incomplete=True)
    assert commands.last_failure is not None
    diagnostic = commands.last_failure["journal_read"]["rejection"]
    assert diagnostic["reason"] == reason
    assert diagnostic["category"] == category
    assert diagnostic["escape_present"] == ("\x1b" in line)
    assert "PRIVATE" not in json.dumps(commands.last_failure)
    assert str(tmp_path) not in json.dumps(commands.last_failure)


@pytest.mark.parametrize("surface", ["persistent", "runtime"])
def test_rejected_read_retains_surface_without_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    import asyncio
    from types import SimpleNamespace

    from observability_infra.smoke_scenarios import Scenario

    commands = Commands(Deadline(3, reserve=0.1))

    def fail(*args: object, **kwargs: object) -> None:
        commands.last_failure = {"journal_read": {}}
        raise RuntimeError("PRIVATE")

    monkeypatch.setattr(commands, "read_journal", fail)
    scenario = object.__new__(Scenario)
    scenario.root, scenario.name, scenario.commands = tmp_path, "journal-enospc", commands
    monkeypatch.setattr(
        scenario, "app", SimpleNamespace(stack=SimpleNamespace(env={})), raising=False
    )
    with pytest.raises(RuntimeError):
        asyncio.run(
            scenario.journal_read(
                tmp_path / ("journal" if surface == "persistent" else "runtime"), "synthetic"
            )
        )
    assert commands.last_failure == {"journal_read": {"surface": surface}}
