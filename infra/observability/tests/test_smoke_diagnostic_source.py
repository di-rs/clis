"""Diagnostic reads select only the verified owned journal/container and separate warnings."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from observability_infra import smoke_telemetry
from observability_infra.smoke_runtime import Commands, Deadline
from observability_infra.smoke_telemetry import Telemetry


def test_diagnostic_selector_requires_owned_namespace_and_exact_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = object.__new__(Telemetry)
    telemetry.root = Path("/var/tmp/boxie-observability-" + "a" * 12)
    telemetry.commands = Commands(Deadline(5, reserve=1))
    checked = []
    monkeypatch.setattr(telemetry, "container", lambda service: "b" * 64)

    def endpoint(service: str, *, expected: str | None = None) -> str:
        assert service == "vector" and expected == "b" * 64
        checked.append("container-network")
        return "unused"

    def journal(root: Path, commands: Commands) -> dict[str, str]:
        assert root == telemetry.root and commands is telemetry.commands
        checked.append("owned-journal")
        return {}

    monkeypatch.setattr(telemetry, "endpoint", endpoint)
    monkeypatch.setattr(smoke_telemetry, "journal_identity", journal)
    with pytest.raises(RuntimeError, match="foreign diagnostic"):
        telemetry.diagnostic_args("vector", expected="c" * 64)
    assert checked == []
    args = telemetry.diagnostic_args("vector", expected="b" * 64, since="@123.5")
    assert checked == ["container-network", "owned-journal"]
    assert args == [
        "journalctl",
        "--namespace=" + telemetry.root.name,
        "--no-pager",
        "--quiet",
        "--all",
        "--output=cat",
        "CONTAINER_ID_FULL=" + "b" * 64,
        "--follow",
        "--lines=all",
        "--since=@123.5",
    ]
    tail = telemetry.diagnostic_args("vector")
    assert tail[-1] == "--lines=100" and "--follow" not in tail


@pytest.mark.parametrize("empty", [False, True])
def test_privacy_tails_are_nonempty_selected_records(empty: bool) -> None:
    telemetry = object.__new__(Telemetry)
    telemetry.root, telemetry.env = Path("/owned"), {}
    telemetry.logs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    def selected_args(
        service: str, *, expected: str | None = None, since: str | None = None
    ) -> list[str]:
        return [service]

    telemetry.diagnostic_args = selected_args  # type: ignore[method-assign]
    calls = []

    def scan(args: list[str], sentinels: tuple[str, ...], **kwargs: object) -> dict[str, int]:
        calls.append(args)
        if args[0] != "journalctl":
            assert kwargs["strict_stderr"] is True and kwargs["max_records"] == 100
            return {"records": int(not empty), "bytes": 8}
        return {"records": 1, "bytes": 16}

    telemetry.commands = SimpleNamespace(scan_privacy=scan)  # type: ignore[assignment]
    if empty:
        with pytest.raises(RuntimeError, match="source empty"):
            asyncio.run(telemetry.inspect_privacy(("PRIVATE",), "sandbox"))
    else:
        result = asyncio.run(telemetry.inspect_privacy(("PRIVATE",), "sandbox"))
        assert result["vector_diagnostic_records"] == result["victorialogs_diagnostic_records"] == 1
        assert calls[-2:] == [["vector"], ["victorialogs"]]


def test_privacy_reader_warning_is_fatal_without_raw_export() -> None:
    commands = Commands(Deadline(5, reserve=1))
    code = (
        "import os; os.write(1,b'harmless\\n'); os.write(2,b'PRIVATE No space left on device\\n')"
    )
    with pytest.raises(RuntimeError, match="privacy journal read failed"):
        commands.scan_privacy(
            [sys.executable, "-c", code], ("secret",), max_records=10, strict_stderr=True
        )
    assert "PRIVATE" not in json.dumps(commands.last_failure)
