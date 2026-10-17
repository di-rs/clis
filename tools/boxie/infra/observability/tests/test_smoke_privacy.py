"""Privacy scanning must finish bounded streams without retaining their contents."""

import json
import os
import sys
from pathlib import Path

import pytest
from observability_infra.smoke_runtime import Commands, Deadline


def scan(script: str, *, records: int = 200) -> dict[str, int]:
    return Commands(Deadline(3, reserve=0.1)).scan_privacy(
        [sys.executable, "-c", script], ("PRIVATE_SENTINEL",), max_records=records
    )


def test_stream_scans_metadata_larger_than_command_capture_limit() -> None:
    line = json.dumps({"MESSAGE": "event", "metadata": "x" * 4096})
    result = scan("print((" + repr(line + "\n") + " * 200), end='')")
    assert result == {"records": 200, "bytes": len((line + "\n").encode()) * 200}
    assert result["bytes"] > 262144


@pytest.mark.parametrize("escaped", [False, True])
def test_stream_checks_secret_across_chunks_and_decoded_json(escaped: bool) -> None:
    line = json.dumps({"MESSAGE": "PRIVATE_SENTINEL"})
    if escaped:
        line = line.replace("P", "\\u0050")
    split = line.index("RIVATE") + 3
    script = (
        "import sys,time; sys.stdout.write("
        + repr(line[:split])
        + "); sys.stdout.flush(); time.sleep(.1); print("
        + repr(line[split:])
        + ")"
    )
    with pytest.raises(RuntimeError, match="privacy") as error:
        scan(script)
    assert "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("print('x' * 65536)", "record limit"),
        ("print('{}\\n' * 201, end='')", "record count"),
        ("print('{}', end='')", "incomplete"),
        ("import sys; print('{}'); sys.exit(4)", "command exit 4"),
    ],
)
def test_stream_rejects_oversized_incomplete_and_failed_scans(script: str, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        scan(script)


def test_stream_byte_budget_and_invalid_encoding_fail_closed() -> None:
    for script, message in (
        ("import sys; sys.stdout.write('x' * 65537)", "limit"),
        ("import sys; sys.stdout.buffer.write(b'\\xff\\n')", "decoding failed"),
    ):
        with pytest.raises(RuntimeError, match=message):
            scan(script, records=1)


def test_stream_timeout_reaps_the_owned_process(tmp_path: Path) -> None:

    # The process writes its PID before holding the pipe open indefinitely.
    pidfile = tmp_path / "scan.pid"
    commands = Commands(Deadline(0.6, reserve=0.1))
    with pytest.raises(TimeoutError):
        commands.scan_privacy(
            [
                sys.executable,
                "-c",
                "import os,time,pathlib; pathlib.Path("
                + repr(str(pidfile))
                + ").write_text(str(os.getpid())); print('{}',flush=True); time.sleep(20)",
            ],
            ("PRIVATE_SENTINEL",),
            max_records=200,
        )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)
