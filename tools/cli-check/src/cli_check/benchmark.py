"""Parse benchmark suites consistently without executing their commands."""

import re
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict, TypeGuard, cast

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class Case(TypedDict):
    name: str
    args: list[str]
    reference_args: NotRequired[list[str]]
    stdin: NotRequired[str]
    prepare: NotRequired[str]
    check: NotRequired[str]
    expected_exit: NotRequired[int]


class Suite(TypedDict):
    binary: str
    cases: list[Case]
    reference: NotRequired[str]
    generate: NotRequired[list[list[str]]]
    env: NotRequired[dict[str, str]]


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def string(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and "\0" not in value


def argv(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(string(item) for item in value)


def fields(table: object, allowed: set[str], location: str) -> dict[str, object]:
    require(isinstance(table, dict), f"{location} must be a table")
    table = cast(dict[str, object], table)
    require(
        not (table.keys() - allowed), f"unknown {location} fields: {sorted(table.keys() - allowed)}"
    )
    return table


def load_suite(path: Path) -> Suite:
    with path.open("rb") as source:
        suite: dict[str, object] = tomllib.load(source)
    fields(suite, {"binary", "reference", "generate", "env", "cases"}, "suite")
    binary = suite.get("binary")
    require(string(binary) and NAME.fullmatch(binary), "binary must be a plain executable name")
    if "reference" in suite:
        require(
            string(suite["reference"]) and suite["reference"],
            "reference must be a nonempty executable path or name",
        )
    recipes = suite.get("generate", [])
    require(
        isinstance(recipes, list) and all(argv(recipe) and recipe for recipe in recipes),
        "generate must contain Biggie argument arrays",
    )
    env = suite.get("env", {})
    require(
        isinstance(env, dict)
        and all(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and string(value)
            for key, value in env.items()
        ),
        "env must contain environment names and string values",
    )
    cases = suite.get("cases")
    require(isinstance(cases, list) and cases, "suite requires at least one case")
    names: set[str] = set()
    for raw_case in cast(list[object], cases):
        case = fields(
            raw_case,
            {"name", "args", "reference_args", "stdin", "prepare", "check", "expected_exit"},
            "case",
        )
        name = case.get("name")
        require(
            string(name) and NAME.fullmatch(name), "case name must be a filename-safe identifier"
        )
        name = cast(str, name)
        require(name.casefold() != "metadata", "metadata is a reserved case name")
        require(name.casefold() not in names, f"duplicate case name: {name}")
        names.add(name.casefold())
        require(argv(case.get("args")), f"{name}: args must be an array of strings")
        if "reference_args" in case:
            require(
                argv(case["reference_args"]), f"{name}: reference_args must be an array of strings"
            )
        for hook in ("prepare", "check"):
            if hook in case:
                require(string(case[hook]), f"{name}: {hook} must be a shell string")
        if "stdin" in case:
            value = case["stdin"]
            require(
                string(value)
                and value
                and not Path(value).is_absolute()
                and ".." not in Path(value).parts,
                f"{name}: stdin must be a relative filename within the working directory",
            )
        expected = case.get("expected_exit", 0)
        require(
            type(expected) is int and 0 <= expected <= 255,
            f"{name}: expected_exit must be an integer from 0 to 255",
        )
    return cast(Suite, suite)
