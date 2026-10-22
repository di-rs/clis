#!/usr/bin/env python3.15
"""Run trusted per-CLI TOML cases with Biggie and hyperfine 2.x."""

import argparse
import filecmp
import json
import math
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, NotRequired, TypedDict, cast

from cli_check.benchmark import Case, Suite, load_suite, require
from cli_check.output import ArgumentParser, emit, failure
from cli_check.workspace import find_root


class Metadata(TypedDict):
    status: str
    started: str
    platform: str
    executables: dict[str, str | None]
    hyperfine_version: str | None
    generate: list[list[str]]
    cases: list[str]
    completed_cases: list[str]
    warmup: int
    runs: int
    timeout: float
    env: dict[str, str]
    error: NotRequired[str]


class Arguments(argparse.Namespace):
    suite: Path
    root: Path | None
    json: bool
    doctor: bool
    candidate: str | None
    baseline: str | None
    reference: str | None
    biggie: str | None
    hyperfine: str
    cases: list[str] | None
    warmup: int
    runs: int
    timeout: float
    output: Path | None
    list: bool


def executable(value: str | Path) -> str:
    path = Path(value).expanduser()
    found = str(path.resolve()) if "/" in str(value) else shutil.which(str(value))
    require(
        found and Path(found).is_file() and os.access(found, os.X_OK),
        f"executable not found: {value}",
    )
    return str(Path(cast(str, found)).resolve())


def execute(
    command: str | list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
    stdin: IO[bytes] | int | None = None,
    stdout: IO[bytes] | int | None = None,
    stderr: IO[bytes] | int | None = None,
    check: bool = True,
) -> int:
    """Bound a subprocess and kill its process group on timeout or interruption."""
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        shell=shell,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    ) as process:
        try:
            returncode = process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, command)
    return returncode


def version(path: str, timeout: float) -> str | None:
    with tempfile.TemporaryFile() as output:
        returncode = execute(
            [path, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=min(5, timeout),
            check=False,
        )
        output.seek(0)
        return output.read(4096).decode("utf-8", "replace").strip() if returncode == 0 else None


def preflight(
    case: Case,
    commands: list[list[str]],
    work: Path,
    captures: Path,
    env: dict[str, str],
    timeout: float,
    progress: int | None = None,
) -> None:
    outputs = []
    for index, command in enumerate(commands):
        if "prepare" in case:
            execute(
                case["prepare"], shell=True, cwd=work, env=env, timeout=timeout, stdout=progress
            )
        output = captures / f"{case['name']}-{index}.stdout"
        error = captures / f"{case['name']}-{index}.stderr"
        source = work / case["stdin"] if "stdin" in case else Path(os.devnull)
        with source.open("rb") as stdin, output.open("wb") as stdout, error.open("wb") as stderr:
            returncode = execute(
                command,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                cwd=work,
                env=env,
                timeout=timeout,
                check=False,
            )
        if returncode != case.get("expected_exit", 0):
            with error.open("rb") as diagnostic:
                detail = diagnostic.read(4096).decode("utf-8", "replace")
            raise ValueError(
                f"{case['name']}: {command[0]} exited {returncode}, "
                f"expected {case.get('expected_exit', 0)}\n{detail}"
            )
        if "check" in case:
            execute(case["check"], shell=True, cwd=work, env=env, timeout=timeout, stdout=progress)
        outputs.append(output)
    if len(outputs) == 2:
        require(
            filecmp.cmp(outputs[0], outputs[1], shallow=False),
            f"{case['name']}: candidate and baseline stdout differ",
        )


def validate_report(path: Path, commands: list[str], runs: int, expected: int) -> None:
    with path.open(encoding="utf-8") as source:
        report: object = json.load(source)
    require(
        isinstance(report, dict) and report.get("schema_version") == 2,
        "expected hyperfine JSON schema 2",
    )
    results = cast(dict[str, object], report).get("results")
    require(
        isinstance(results, list) and len(results) == len(commands),
        "hyperfine report has incomplete results",
    )
    for result, command in zip(cast(list[object], results), commands, strict=False):
        require(
            isinstance(result, dict) and result.get("command") == command,
            "hyperfine report command mismatch",
        )
        samples = cast(dict[str, object], result).get("measurements")
        require(
            isinstance(samples, list) and len(samples) == runs,
            "hyperfine report has incomplete measurements",
        )
        require(
            all(
                isinstance(sample, dict)
                and type(sample.get("exit_code")) is int
                and sample["exit_code"] == expected
                for sample in cast(list[object], samples)
            ),
            "hyperfine measured an unexpected exit code",
        )


def workspace_root(args: Arguments) -> Path:
    if args.root:
        return args.root.resolve()
    try:
        return find_root(args.suite.resolve().parent)
    except ValueError:
        return Path.cwd()


def run(args: Arguments, suite: Suite, cases: list[Case]) -> Path:
    root = workspace_root(args)
    candidate = executable(args.candidate or root / "target/release" / suite["binary"])
    other = args.baseline or args.reference or suite.get("reference")
    baseline = executable(other) if other else None
    biggie = (
        executable(args.biggie or root / "target/release/biggie") if suite.get("generate") else None
    )
    hyperfine = executable(args.hyperfine)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    output = (args.output or root / "target/benchmarks" / f"{suite['binary']}-{stamp}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata: Metadata = {
        "status": "running",
        "started": stamp,
        "platform": platform.platform(),
        "executables": {
            "candidate": candidate,
            "baseline": baseline,
            "biggie": biggie,
            "hyperfine": hyperfine,
        },
        "hyperfine_version": None,
        "generate": suite.get("generate", []),
        "cases": [case["name"] for case in cases],
        "completed_cases": [],
        "warmup": args.warmup,
        "runs": args.runs,
        "timeout": args.timeout,
        "env": {
            "LC_ALL": "C",
            "TZ": "UTC",
            "SANCTUARY_LOG_LEVEL": "off",
            "SANCTUARY_LOG_STDERR": "off",
        }
        | suite.get("env", {}),
    }

    def save_metadata() -> None:
        (output / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )

    save_metadata()
    try:
        shutil.copyfile(args.suite, output / "suite.toml")
        hyperfine_version = version(hyperfine, args.timeout)
        metadata["hyperfine_version"] = hyperfine_version
        require(
            hyperfine_version and re.match(r"hyperfine 2\.", hyperfine_version),
            "hyperfine 2.x is required",
        )
        env = os.environ | metadata["env"]
        with tempfile.TemporaryDirectory(prefix="cli-bench-") as temporary_name:
            temporary = Path(temporary_name).resolve()
            work, staging, captures = [
                temporary / name for name in ("work", "reports", "preflight")
            ]
            for directory in (work, staging, captures):
                directory.mkdir()
            for recipe in suite.get("generate", []):
                execute(
                    [cast(str, biggie), *recipe],
                    cwd=work,
                    env=env,
                    timeout=args.timeout,
                    stdout=sys.stderr.fileno() if args.json else None,
                )
            for case in cases:
                print(
                    f"Benchmarking {case['name']}",
                    flush=True,
                    file=sys.stderr if args.json else sys.stdout,
                )
                commands = [[candidate, *case["args"]]]
                if baseline:
                    reference_args = (
                        case["args"] if args.baseline else case.get("reference_args", case["args"])
                    )
                    commands.insert(0, [baseline, *reference_args])
                preflight(
                    case,
                    commands,
                    work,
                    captures,
                    env,
                    args.timeout,
                    sys.stderr.fileno() if args.json else None,
                )
                shell_commands = [shlex.join(command) for command in commands]
                json_path, markdown_path = (
                    staging / f"{case['name']}.json",
                    staging / f"{case['name']}.md",
                )
                invocation = [
                    hyperfine,
                    "--shell=none",
                    "--output=pipe",
                    "--warmup",
                    str(args.warmup),
                    "--runs",
                    str(args.runs),
                    "--export-json",
                    str(json_path),
                    "--export-markdown",
                    str(markdown_path),
                ]
                if "stdin" in case:
                    invocation += ["--input", str(work / case["stdin"])]
                if "prepare" in case:
                    invocation += ["--prepare", case["prepare"]]
                expected = case.get("expected_exit", 0)
                if expected:
                    invocation += [f"--ignore-failure={expected}"]
                if baseline:
                    invocation += ["--command-name", "baseline" if args.baseline else "reference"]
                invocation += ["--command-name", "candidate", "--", *shell_commands]
                execute(
                    invocation,
                    cwd=work,
                    env=env,
                    timeout=args.timeout,
                    stdout=sys.stderr.fileno() if args.json else None,
                )
                validate_report(json_path, shell_commands, args.runs, expected)
                require(
                    markdown_path.is_file() and markdown_path.stat().st_size,
                    "hyperfine Markdown export is missing or empty",
                )
                for report in (json_path, markdown_path):
                    shutil.copyfile(report, output / report.name)
                metadata["completed_cases"].append(case["name"])
                save_metadata()
        metadata["status"] = "complete"
    except (Exception, KeyboardInterrupt) as error:
        metadata.update({"status": "failed", "error": str(error) or "interrupted"})
        raise
    finally:
        save_metadata()
    if not args.json:
        print(f"Results: {output}")
    return output


def doctor(args: Arguments, suite: Suite | None) -> int:
    root = workspace_root(args)
    requested: dict[str, str | Path] = {"hyperfine": args.hyperfine}
    if suite:
        requested["candidate"] = args.candidate or root / "target/release" / suite["binary"]
        if suite.get("generate"):
            requested["biggie"] = args.biggie or root / "target/release/biggie"
        other = args.baseline or args.reference or suite.get("reference")
        if other:
            requested["comparison"] = other
    checks: list[dict[str, object]] = []
    for name, value in requested.items():
        try:
            path = executable(value)
            if name == "hyperfine":
                installed = version(path, args.timeout)
                require(
                    installed and re.match(r"hyperfine 2\.", installed), "hyperfine 2.x is required"
                )
            checks.append({"name": name, "ok": True, "path": path})
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            checks.append({"name": name, "ok": False, "message": str(error)})
    ok = all(check["ok"] for check in checks)
    data = {
        "version": "0.1.0",
        "mode": "local",
        "auth": "not_required",
        "root": str(root),
        "checks": checks,
    }
    if args.json:
        emit(data, ok=ok)
    else:
        for check in checks:
            print(f"{check['name']}: {check.get('path', check.get('message'))}")
    return 0 if ok else 1


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path, help="TOML suite, or doctor to check dependencies")
    parser.add_argument("--root", type=Path, help="workspace for default executable/report paths")
    parser.add_argument("--json", action="store_true", help="emit structured results and errors")
    parser.add_argument(
        "--doctor", action="store_true", help="check suite dependencies without executing cases"
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument(
        "--candidate", help="candidate executable (default: target/release/<binary>)"
    )
    comparison = parser.add_mutually_exclusive_group()
    comparison.add_argument("--baseline", help="previous executable, using candidate arguments")
    comparison.add_argument("--reference", help="override the suite's system reference executable")
    parser.add_argument("--biggie", help="generator executable (default: target/release/biggie)")
    parser.add_argument("--hyperfine", default="hyperfine", help="hyperfine 2.x executable")
    parser.add_argument("--case", action="append", dest="cases", help="select a case (repeatable)")
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="seconds per generator, hook, preflight or complete hyperfine case (default: 300)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new report directory (default: target/benchmarks/<binary>-<timestamp>)",
    )
    parser.add_argument("--list", action="store_true", help="list cases without running tools")
    args = parser.parse_args(namespace=Arguments())
    try:
        require(
            math.isfinite(args.timeout) and args.timeout > 0,
            "timeout must be a finite positive number of seconds",
        )
        require(
            args.warmup >= 0 and args.runs > 0,
            "warmup must be nonnegative and runs must be positive",
        )
        if args.suite == Path("doctor"):
            return doctor(args, None)
        suite = load_suite(args.suite)
        if args.doctor:
            return doctor(args, suite)
        names = {case["name"] for case in suite["cases"]}
        require(
            not (set(args.cases or []) - names),
            f"unknown cases: {sorted(set(args.cases or []) - names)}",
        )
        cases = [case for case in suite["cases"] if not args.cases or case["name"] in args.cases]
        if args.list:
            if args.json:
                emit({"binary": suite["binary"], "cases": cases})
            else:
                print("\n".join(case["name"] for case in cases))
        else:
            output = run(args, suite, cases)
            if args.json:
                emit(
                    {
                        "output": str(output),
                        "metadata": str(output / "metadata.json"),
                        "completed_cases": [case["name"] for case in cases],
                    }
                )
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if args.json:
            failure("operation", str(error))
        else:
            print(f"cli-bench: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if args.json:
            failure("interrupted", "interrupted")
        else:
            print("cli-bench: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
