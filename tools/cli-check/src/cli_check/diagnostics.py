"""Stable, path-sorted standards diagnostics."""

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Diagnostic:
    path: str
    group: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.group}:{self.rule}]"
