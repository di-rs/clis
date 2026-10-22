#!/usr/bin/env python3.15
"""Compatibility entry point; prefer the installed cli-bench command."""

import sys

from cli_bench.command import main

if __name__ == "__main__":
    sys.exit(main())
