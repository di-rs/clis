"""Compare clone fragments with the PR merge base."""

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from .changes import arguments, contents, files, source, summary
from .scripts import script


def snapshot(ref: str, destination: Path) -> None:
    for path in files(ref):
        if source(path):
            target = destination / path
            if target.suffix == ".svelte":
                target = target.with_suffix(".svelte.ts")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(script(path, contents(ref, path)))


def clones(ref: str, destination: Path) -> Counter[str]:
    snapshot(ref, destination)
    subprocess.run(
        [
            "npx",
            "--yes",
            "jscpd@4.0.5",
            "--min-lines",
            "5",
            "--min-tokens",
            "50",
            "--reporters",
            "json",
            "--silent",
            "--output",
            str(destination / "report"),
            str(destination),
        ],
        check=True,
    )
    data = json.loads((destination / "report/jscpd-report.json").read_text())
    return Counter(
        hashlib.sha256(re.sub(r"\s+", " ", item["fragment"]).encode()).hexdigest()
        for item in data["duplicates"]
    )


def duplication(base: str, head: str) -> None:
    with TemporaryDirectory() as temp:
        before = clones(base, Path(temp) / "base")
        after = clones(head, Path(temp) / "head")
    count = sum((after - before).values())
    summary(
        f"## New duplication (advisory)\n\n**{count}** additional clone pairs "
        "(minimum 5 lines / 50 tokens). Existing clone pairs are subtracted by normalized "
        "fragment, so shifted line numbers do not count as new duplication. "
        "Generated code and designated fixture directories are excluded."
    )


if __name__ == "__main__":
    args = arguments()
    duplication(args.base, args.head)
