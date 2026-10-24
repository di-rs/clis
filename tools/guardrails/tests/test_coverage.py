"""Changed-line coverage accounting."""

from pathlib import Path

import pytest


def test_coverage_counts_changed_executable_lines_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from guardrails import rust_coverage as reports

    monkeypatch.setattr(reports, "changes", lambda base, head: {"src/lib.rs": [3, 4, 5]})
    report = tmp_path / "coverage.info"
    report.write_text(
        "SF:src/lib.rs\nDA:1,5\nDA:3,0\nDA:4,1\nend_of_record\nSF:src/lib.rs\nDA:4,1\nend_of_record\n"
    )
    reports.coverage("base", "head", report)
    assert "1/2: **50.0%**" in capsys.readouterr().out
