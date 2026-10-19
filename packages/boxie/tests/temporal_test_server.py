"""Checksum-verified test server input, independent of database fixture imports."""

import hashlib
import json
import os
import platform
from pathlib import Path

VARIANTS = {("Darwin", "arm64"): "macOS_arm64", ("Linux", "x86_64"): "linux_amd64"}
PINS = json.loads((Path(__file__).with_name("test-server-pins.json")).read_text())["artifacts"]


def test_server_path() -> Path:
    path = Path(os.environ.get("BOXIE_TEST_SERVER", "/missing/BOXIE_TEST_SERVER"))
    variant = VARIANTS.get((platform.system(), platform.machine()))
    expected = next((entry["binary_sha256"] for entry in PINS if entry["variant"] == variant), None)
    if expected is None or not path.is_file():
        raise RuntimeError(
            "set BOXIE_TEST_SERVER to the pinned 1.40.0 executable for this platform"
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("test server executable checksum differs from pinned 1.40.0")
    return path
