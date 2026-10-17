"""Pulumi entry point; the typed environment composition lives in stack.py."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("stack.py")))
