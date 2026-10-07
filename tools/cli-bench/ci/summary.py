"""Project existing Rust observations into small, ephemeral workflow outputs.

This read-only measurement-side adapter has no network or GitHub credentials.
It never exports bundles or recomputes statistics. The comment job validates the
compact interface independently before it renders anything.
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess

PACKAGES = ('biggie', 'tailr', 'mkdirr')
LIMIT = 24000
CASE_LIMIT = 12


def encode(value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if len(raw.encode()) > LIMIT:
        raise ValueError('summary exceeds 24000 bytes')
    return raw


def label(value):
    if not isinstance(value, str):
        raise ValueError('expected text')
    # Explicit truncation is display-only; original observations remain local.
    return value if len(value) <= 128 else value[:125] + '...'


def number(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError('invalid observation')
    return value


def project(package, record, failure):
    result = {'package': package, 'outcome': 'unavailable', 'failure': None,
              'cases': [], 'omitted_cases': 0, 'issues': []}
    if failure is not None:
        result['failure'] = label(failure['stage'] + ': ' + failure['message'])
    if record is None:
        result['failure'] = result['failure'] or 'No sealed measurement report; inspect workflow logs.'
        return result
    publication = record['publication']
    result['outcome'] = publication['result']['outcome']
    result['issues'] = [label(issue) for issue in publication['analysis']['issues'][:4]]
    cases = publication['analysis']['cases']
    result['omitted_cases'] = max(0, len(cases) - CASE_LIMIT)
    for case in cases[:CASE_LIMIT]:
        roles = []
        for role, data in case['roles'].items():
            roles.append({'role': role, 'elapsed_mean': number(data['elapsed']['mean'] if data['elapsed'] else None),
                          'rss_mean': number(data['rss']['mean'] if data['rss'] else None),
                          'executable_bytes': number(data['executable_bytes'])})
        comparisons = []
        for comparison in case['comparisons']:
            comparisons.append({key: comparison[key] for key in
                                ('baseline', 'candidate', 'ratio', 'elapsed_change_percent', 'direction')})
        result['cases'].append({'id': label(case['id']), 'roles': roles, 'comparisons': comparisons})
    encode(result)
    return result


def finalize(root, cli, packages, platform, profile):
    result = {'version': 1, 'platform': platform, 'profile': profile, 'suites': []}
    for package in packages:
        failure = None
        record = None
        status = root / 'status' / (package + '.json')
        try:
            if status.is_file():
                failure = json.loads(status.read_text())
            if cli.is_file():
                # Full local history stays in the ephemeral runner. Only the
                # existing Rust projector reads and validates sealed evidence.
                path = root / (package + '-history.json')
                with path.open('wb') as output:
                    subprocess.run([str(cli), 'history', '-d', str(root / 'data' / 'runs'), '-s', package, '-f', 'json'],
                                   stdout=output, stderr=subprocess.DEVNULL, timeout=120, check=True)
                with path.open('rb') as source:
                    raw = source.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError('local projection too large')
                records = json.loads(raw)
                if not isinstance(records, list) or len(records) != 1:
                    raise ValueError('expected one sealed run')
                record = records[0]
            entry = project(package, record, failure)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            entry = project(package, None, {'stage': 'projection', 'message': 'Report unavailable; inspect workflow logs.'})
        result['suites'].append(entry)
    # Keep every suite's status even if long data consumes the transport budget.
    while True:
        try:
            encode(result)
            return result
        except ValueError:
            largest = max(result['suites'], key=lambda suite: len(suite['cases']), default=None)
            if largest is None or not largest['cases']:
                raise
            largest['cases'].pop()
            largest['omitted_cases'] += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--profile', choices=('smoke', 'full'), required=True)
    parser.add_argument('--platform', choices=('linux', 'macos'), required=True)
    args = parser.parse_args()
    packages = json.loads(os.environ['BENCH_PACKAGES'])
    if not isinstance(packages, list) or len(packages) != len(set(packages)) or any(p not in PACKAGES for p in packages):
        raise ValueError('invalid selected packages')
    result = finalize(args.root.resolve(), args.cli.resolve(), packages, args.platform, args.profile)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('summary=' + encode(result) + '\n')
    return int(any(suite['failure'] or suite['outcome'] != 'complete' for suite in result['suites']))


if __name__ == '__main__':
    raise SystemExit(main())
