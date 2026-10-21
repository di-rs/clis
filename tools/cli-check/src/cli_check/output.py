"""Shared stdout JSON contract for repository commands."""

import argparse
import json
import sys
from typing import NoReturn


def wants_json() -> bool:
    args = sys.argv[1:]
    return "--json" in args[: args.index("--")] if "--" in args else "--json" in args


def emit(data: object, *, ok: bool = True) -> None:
    print(json.dumps({"ok": ok, "data": data}, ensure_ascii=True))


def failure(kind: str, message: str) -> None:
    print(json.dumps({"ok": False, "error": {"kind": kind, "message": message}}))


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        if wants_json():
            failure("arguments", message)
            raise SystemExit(2)
        super().error(message)
