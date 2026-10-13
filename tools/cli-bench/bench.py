#!/usr/bin/env python3
"""Run trusted per-CLI TOML cases with Biggie and hyperfine 2.x."""

import sys

if sys.version_info < (3, 11):
    sys.exit("cli-bench requires Python 3.11 or newer (for example, python3.14).")

import argparse
from datetime import datetime, timezone
import filecmp
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def string(value):
    return isinstance(value, str) and "\0" not in value


def argv(value):
    return isinstance(value, list) and all(string(item) for item in value)


def fields(table, allowed, location):
    require(isinstance(table, dict), f"{location} must be a table")
    require(not (table.keys() - allowed), f"unknown {location} fields: {sorted(table.keys() - allowed)}")


def load_suite(path):
    with path.open("rb") as source:
        suite = tomllib.load(source)
    fields(suite, {"binary", "reference", "generate", "env", "cases"}, "suite")
    require(string(suite.get("binary")) and NAME.fullmatch(suite["binary"]), "binary must be a plain executable name")
    if "reference" in suite:
        require(string(suite["reference"]) and suite["reference"], "reference must be a nonempty executable path or name")
    recipes = suite.get("generate", [])
    require(isinstance(recipes, list) and all(argv(recipe) and recipe for recipe in recipes), "generate must contain Biggie argument arrays")
    env = suite.get("env", {})
    require(isinstance(env, dict) and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and string(value)
                                        for key, value in env.items()), "env must contain environment names and string values")
    require(isinstance(suite.get("cases"), list) and suite["cases"], "suite requires at least one case")
    names = set()
    for case in suite["cases"]:
        fields(case, {"name", "args", "reference_args", "stdin", "prepare", "check", "expected_exit"}, "case")
        name = case.get("name")
        require(string(name) and NAME.fullmatch(name), "case name must be a filename-safe identifier")
        require(name.casefold() != "metadata", "metadata is a reserved case name")
        require(name.casefold() not in names, f"duplicate case name: {name}")
        names.add(name.casefold())
        require(argv(case.get("args")), f"{name}: args must be an array of strings")
        if "reference_args" in case:
            require(argv(case["reference_args"]), f"{name}: reference_args must be an array of strings")
        for hook in ("prepare", "check"):
            if hook in case:
                require(string(case[hook]), f"{name}: {hook} must be a shell string")
        if "stdin" in case:
            value = case["stdin"]
            require(string(value) and value and not Path(value).is_absolute() and ".." not in Path(value).parts,
                    f"{name}: stdin must be a relative filename within the working directory")
        expected = case.get("expected_exit", 0)
        require(type(expected) is int and 0 <= expected <= 255, f"{name}: expected_exit must be an integer from 0 to 255")
    return suite


def executable(value):
    path = Path(value).expanduser()
    found = str(path.resolve()) if "/" in str(value) else shutil.which(str(value))
    require(found and Path(found).is_file() and os.access(found, os.X_OK), f"executable not found: {value}")
    return str(Path(found).resolve())


def version(path):
    try:
        result = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        return result.stdout[:4096].decode("utf-8", "replace").strip() if result.returncode == 0 else None
    except subprocess.TimeoutExpired:
        return None


def preflight(case, commands, work, captures, env):
    outputs = []
    for index, command in enumerate(commands):
        if "prepare" in case:
            subprocess.run(case["prepare"], shell=True, cwd=work, env=env, check=True)
        output = captures / f"{case['name']}-{index}.stdout"
        error = captures / f"{case['name']}-{index}.stderr"
        source = work / case["stdin"] if "stdin" in case else Path(os.devnull)
        with source.open("rb") as stdin, output.open("wb") as stdout, error.open("wb") as stderr:
            result = subprocess.run(command, stdin=stdin, stdout=stdout, stderr=stderr, cwd=work, env=env)
        if result.returncode != case.get("expected_exit", 0):
            with error.open("rb") as diagnostic:
                detail = diagnostic.read(4096).decode("utf-8", "replace")
            raise ValueError(f"{case['name']}: {command[0]} exited {result.returncode}, "
                             f"expected {case.get('expected_exit', 0)}\n{detail}")
        if "check" in case:
            subprocess.run(case["check"], shell=True, cwd=work, env=env, check=True)
        outputs.append(output)
    if len(outputs) == 2:
        require(filecmp.cmp(*outputs, shallow=False), f"{case['name']}: candidate and baseline stdout differ")


def validate_report(path, commands, runs, expected):
    with path.open(encoding="utf-8") as source:
        report = json.load(source)
    require(isinstance(report, dict) and report.get("schema_version") == 2, "expected hyperfine JSON schema 2")
    results = report.get("results")
    require(isinstance(results, list) and len(results) == len(commands), "hyperfine report has incomplete results")
    for result, command in zip(results, commands):
        require(isinstance(result, dict) and result.get("command") == command, "hyperfine report command mismatch")
        samples = result.get("measurements")
        require(isinstance(samples, list) and len(samples) == runs, "hyperfine report has incomplete measurements")
        require(all(isinstance(sample, dict) and type(sample.get("exit_code")) is int
                    and sample["exit_code"] == expected for sample in samples), "hyperfine measured an unexpected exit code")


def run(args, suite, cases):
    candidate = executable(args.candidate or ROOT / "target/release" / suite["binary"])
    other = args.baseline or args.reference or suite.get("reference")
    baseline = executable(other) if other else None
    biggie = executable(args.biggie or ROOT / "target/release/biggie") if suite.get("generate") else None
    hyperfine = executable(args.hyperfine)
    hyperfine_version = version(hyperfine)
    require(hyperfine_version and re.match(r"hyperfine 2\.", hyperfine_version), "hyperfine 2.x is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = (args.output or ROOT / "target/benchmarks" / f"{suite['binary']}-{stamp}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"status": "running", "started": stamp, "platform": platform.platform(),
                "executables": {"candidate": candidate, "baseline": baseline, "biggie": biggie, "hyperfine": hyperfine},
                "hyperfine_version": hyperfine_version, "generate": suite.get("generate", []),
                "cases": [case["name"] for case in cases], "completed_cases": [],
                "warmup": args.warmup, "runs": args.runs,
                "env": {"LC_ALL": "C", "TZ": "UTC", "CLIS_LOG_LEVEL": "off"} | suite.get("env", {})}

    def save_metadata():
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    save_metadata()
    try:
        shutil.copyfile(args.suite, output / "suite.toml")
        env = os.environ | metadata["env"]
        with tempfile.TemporaryDirectory(prefix="cli-bench-") as temporary:
            temporary = Path(temporary).resolve()
            work, staging, captures = [temporary / name for name in ("work", "reports", "preflight")]
            for directory in (work, staging, captures):
                directory.mkdir()
            for recipe in suite.get("generate", []):
                subprocess.run([biggie, *recipe], cwd=work, env=env, check=True)
            for case in cases:
                print(f"Benchmarking {case['name']}", flush=True)
                commands = [[candidate, *case["args"]]]
                if baseline:
                    reference_args = case["args"] if args.baseline else case.get("reference_args", case["args"])
                    commands.insert(0, [baseline, *reference_args])
                preflight(case, commands, work, captures, env)
                commands = [shlex.join(command) for command in commands]
                json_path, markdown_path = staging / f"{case['name']}.json", staging / f"{case['name']}.md"
                invocation = [hyperfine, "--shell=none", "--output=pipe", "--warmup", str(args.warmup),
                              "--runs", str(args.runs), "--export-json", str(json_path), "--export-markdown", str(markdown_path)]
                if "stdin" in case:
                    invocation += ["--input", str(work / case["stdin"])]
                if "prepare" in case:
                    invocation += ["--prepare", case["prepare"]]
                expected = case.get("expected_exit", 0)
                if expected:
                    invocation += [f"--ignore-failure={expected}"]
                if baseline:
                    invocation += ["--command-name", "baseline" if args.baseline else "reference"]
                invocation += ["--command-name", "candidate", "--", *commands]
                subprocess.run(invocation, cwd=work, env=env, check=True)
                validate_report(json_path, commands, args.runs, expected)
                require(markdown_path.is_file() and markdown_path.stat().st_size, "hyperfine Markdown export is missing or empty")
                for report in (json_path, markdown_path):
                    shutil.copyfile(report, output / report.name)
                metadata["completed_cases"].append(case["name"])
                save_metadata()
        metadata["status"] = "complete"
    except (Exception, KeyboardInterrupt) as error:
        metadata.update(status="failed", error=str(error) or "interrupted")
        raise
    finally:
        save_metadata()
    print(f"Results: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("--candidate", help="candidate executable (default: target/release/<binary>)")
    comparison = parser.add_mutually_exclusive_group()
    comparison.add_argument("--baseline", help="previous executable, using candidate arguments")
    comparison.add_argument("--reference", help="override the suite's system reference executable")
    parser.add_argument("--biggie", help="generator executable (default: target/release/biggie)")
    parser.add_argument("--hyperfine", default="hyperfine", help="hyperfine 2.x executable")
    parser.add_argument("--case", action="append", dest="cases", help="select a case (repeatable)")
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, help="new report directory (default: target/benchmarks/<binary>-<timestamp>)")
    parser.add_argument("--list", action="store_true", help="list cases without running tools")
    args = parser.parse_args()
    try:
        require(args.warmup >= 0 and args.runs > 0, "warmup must be nonnegative and runs must be positive")
        suite = load_suite(args.suite)
        names = {case["name"] for case in suite["cases"]}
        require(not (set(args.cases or []) - names), f"unknown cases: {sorted(set(args.cases or []) - names)}")
        cases = [case for case in suite["cases"] if not args.cases or case["name"] in args.cases]
        if args.list:
            print("\n".join(case["name"] for case in cases))
        else:
            run(args, suite, cases)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"cli-bench: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("cli-bench: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
