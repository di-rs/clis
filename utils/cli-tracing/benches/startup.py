"""Compare retained release Biggie binaries; validate every timed invocation."""

import argparse
import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("before", type=Path)
parser.add_argument("after", type=Path)
args = parser.parse_args()
binaries = {"before": args.before.resolve(), "after": args.after.resolve()}
env = {key: value for key, value in os.environ.items() if not key.startswith("CLIS_")}
rows = []
with tempfile.TemporaryDirectory(prefix="clis-startup-") as directory:
    data = Path(directory) / "data.txt"
    for repetition in range(55):
        order = ["before", "after"] if repetition % 2 == 0 else ["after", "before"]
        for mode in order:
            data.unlink(missing_ok=True)
            started = time.perf_counter_ns()
            result = subprocess.run(
                [str(binaries[mode]), str(data), "-n", "1"],
                env=env, capture_output=True, check=True,
            )
            elapsed = time.perf_counter_ns() - started
            # All correctness checks are outside the measured interval.
            if result.stderr or result.stdout != f'Done, wrote 1 line to "{data}".\n'.encode():
                raise RuntimeError(f"unexpected command output: {result}")
            contents = data.read_bytes()
            if not contents.endswith(b"\n") or contents.count(b"\n") != 1:
                raise RuntimeError("incorrect line boundaries")
            words = contents[:-1].split(b" ")
            if not (7 <= len(words) <= 14 and all(
                2 <= len(word) <= 11 and word.isascii() and word.isalnum()
                for word in words
            )):
                raise RuntimeError("incorrect generated words")
            if repetition >= 5:
                rows.append([mode, repetition - 5, elapsed])
writer = csv.writer(sys.stdout, lineterminator="\n")
writer.writerow(["mode", "repetition", "elapsed_ns"])
writer.writerows(rows)
