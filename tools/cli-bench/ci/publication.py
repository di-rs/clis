"""Bound CI transport around Rust-validated HistoryRecord; never compute statistics.

This producer is untrusted. The publisher must validate the complete nested Rust
schema independently and bind identity/expiry to authoritative GitHub API data.
"""
import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tomllib

from selection import sha

PUBLICATION_LIMIT = 16 * 1024 * 1024
EVIDENCE_LIMIT = 512 * 1024 * 1024
PACKAGES = ('biggie', 'tailr', 'mkdirr')
ENVELOPE_FIELDS = set('schema_version repository run_id run_attempt platform measurement_profile candidate_sha target_sha previous_sha requested_retention_days expires_at_unix_seconds suites omissions'.split())
HISTORY_FIELDS = set('schema_version publication submitted_suite resolved_suite checksums generations tools pipeline experiment attempt diagnostics omissions'.split())
PUBLICATION_FIELDS = set('schema_version comparison manifest result analysis validation evidence attempt replay requested_retention_days expires_at_unix_seconds'.split())
MANIFEST_FIELDS = set('schema_version execution_kind run_id contract roles inputs selected_cases host tool_paths experiment'.split())
STAGES = ('selection', 'setup', 'build', 'measurement', 'projection', 'artifact')


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('missing or unknown transport fields')


def text(value, limit=4096):
    if not isinstance(value, str) or len(value.encode('utf-8')) > limit:
        raise ValueError('invalid or oversized transport string')


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('invalid transport integer')


def validate(value):
    fields(value, ENVELOPE_FIELDS)
    integer(value['schema_version'], 1, 1)
    if not isinstance(value['repository'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}', value['repository']):
        raise ValueError('invalid repository')
    if not isinstance(value['run_id'], str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value['run_id']):
        raise ValueError('invalid run ID')
    integer(value['run_attempt'], 1, 1_000_000)
    if value['platform'] not in ('linux', 'macos') or value['measurement_profile'] not in ('full', 'smoke'):
        raise ValueError('invalid platform/profile')
    for name in ('candidate_sha', 'target_sha', 'previous_sha'):
        if value[name] is not None:
            sha(value[name])
    integer(value['requested_retention_days'], 90, 90)
    if value['expires_at_unix_seconds'] is not None:
        raise ValueError('producer cannot assert effective artifact expiry')
    if not isinstance(value['omissions'], list) or len(value['omissions']) > 128:
        raise ValueError('invalid omissions')
    for omission in value['omissions']:
        text(omission)
    suites = value['suites']
    if not isinstance(suites, list) or len(suites) != 3:
        raise ValueError('expected three suite entries')
    seen = set()
    for suite in suites:
        fields(suite, {'package', 'records', 'failure'})
        package = suite['package']
        if not isinstance(package, str) or package not in PACKAGES or package in seen:
            raise ValueError('invalid/duplicate package')
        seen.add(package)
        failure = suite['failure']
        if failure is not None:
            fields(failure, {'stage', 'message', 'exit_code'})
            if failure['stage'] not in STAGES:
                raise ValueError('invalid failure stage')
            text(failure['message'])
            if failure['exit_code'] is not None:
                integer(failure['exit_code'], -255, 255)
        records = suite['records']
        if not isinstance(records, list) or len(records) > 1 or (not records and failure is None):
            raise ValueError('one sealed record or explicit failure required')
        for record in records:
            fields(record, HISTORY_FIELDS)
            integer(record['schema_version'], 1, 1)
            pub = record['publication']
            fields(pub, PUBLICATION_FIELDS)
            integer(pub['schema_version'], 1, 1)
            manifest = pub['manifest']
            fields(manifest, MANIFEST_FIELDS)
            integer(manifest['schema_version'], 1, 1)
            if manifest['execution_kind'] != 'measure':
                raise ValueError('only Measure records belong in this transport')
            contract = manifest['contract']
            if contract is not None:
                if contract['profile'] != value['measurement_profile'] or contract['suite']['package'] != package:
                    raise ValueError('record profile/package differs from envelope')
            elif failure is None:
                raise ValueError('unresolved record cannot be success')
            for source in ('submitted_suite', 'resolved_suite'):
                text(record[source], 8 * 1024 * 1024)
                if tomllib.loads(record[source]).get('package') != package:
                    raise ValueError('history suite package differs from envelope')
            fields(pub['result'], {'schema_version', 'outcome', 'message'})
            if pub['result']['outcome'] not in ('complete', 'failed', 'incomplete'):
                raise ValueError('invalid outcome')
            if pub['result']['outcome'] != 'complete' and failure is None:
                raise ValueError('failed history requires explicit command failure')
            if value['candidate_sha'] is None or value['previous_sha'] is None:
                raise ValueError('sealed record requires selected revisions')


def encode(value):
    validate(value)
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode() + b'\n'
    if len(raw) > PUBLICATION_LIMIT:
        raise ValueError('aggregate publication exceeds 16 MiB')
    return raw


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('non-finite JSON value: ' + value)


def read_json(raw):
    if len(raw) > PUBLICATION_LIMIT:
        raise ValueError('JSON exceeds 16 MiB')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


def decode(raw):
    value = read_json(raw)
    validate(value)
    return value


def tree_bytes(root, limit):
    total = 0
    for path in root.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError('nonregular evidence member')
        total += path.stat().st_size
        if total > limit:
            raise ValueError('full evidence exceeds 512 MiB aggregate cap')
    return total


def failure(stage, message, code=None):
    return {'stage': stage, 'message': message[:1024], 'exit_code': code}


def finalize(root, cli, identity, profile, platform, setup_stage):
    """Project all suites, preserving failures and small metadata if export fails."""
    value = {'schema_version': 1, 'repository': identity['repository'], 'run_id': identity['run_id'],
             'run_attempt': identity['run_attempt'], 'platform': platform, 'measurement_profile': profile,
             'candidate_sha': None, 'target_sha': None, 'previous_sha': None,
             'requested_retention_days': 90, 'expires_at_unix_seconds': None, 'suites': [],
             'omissions': ['Datasets and executables are omitted from CI bundles. Exact original local resources are required for replay.',
                           'Effective artifact expiry is unavailable until confirmed by the GitHub artifact API.']}
    if (root / 'selection.json').is_file():
        selected = read_json((root / 'selection.json').read_bytes())
        fields(selected, {'candidate_sha', 'target_sha', 'previous_sha'})
        value.update(selected)
    full = root / 'full'
    full.mkdir(parents=True, exist_ok=True)
    used = tree_bytes(full, EVIDENCE_LIMIT) + PUBLICATION_LIMIT
    for package in PACKAGES:
        entry = {'package': package, 'records': [], 'failure': None}
        status_path = root / 'status' / f'{package}.json'
        if status_path.exists():
            entry['failure'] = read_json(status_path.read_bytes())
        else:
            entry['failure'] = failure(setup_stage, 'No completed measurement command; inspect workflow setup/build logs.')
        if cli.is_file() and (root / 'data' / 'runs').is_dir():
            try:
                # Rust loads/verifies sealed evidence and performs its sole history projection.
                projected_path = root / f'{package}-history.json'
                with projected_path.open('wb') as projected:
                    subprocess.run([str(cli), 'history', '-d', str(root / 'data' / 'runs'), '-s', package, '-f', 'json'],
                                   stdout=projected, stderr=subprocess.PIPE, timeout=120, check=True)
                with projected_path.open('rb') as projected:
                    entry['records'] = read_json(projected.read(PUBLICATION_LIMIT + 1))
                if not entry['records'] and entry['failure'] is None:
                    entry['failure'] = failure('projection', 'Successful command did not produce a sealed run.')
                for record in entry['records']:
                    run_id = record['publication']['manifest']['run_id']
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', run_id):
                        raise ValueError('invalid local run identifier')
                    run = root / 'data' / 'runs' / run_id
                    (root / 'exports').mkdir(exist_ok=True)
                    destination = root / 'exports' / package
                    subprocess.run([str(cli), 'export', '-i', str(run), '-o', str(destination)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120, check=True)
                    size = tree_bytes(destination, EVIDENCE_LIMIT - used)
                    shutil.copytree(destination, full / package)
                    used += size
            except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
                value['omissions'].append(f'{package}: full export/projection unavailable: {str(error)[:512]}')
                if entry['failure'] is None:
                    entry['failure'] = failure('artifact', 'Full export or projection failed; see omissions.')
        value['suites'].append(entry)
    for log in sorted((root / 'logs').glob('*')):
        if not log.is_file() or log.is_symlink():
            continue
        # Retain bounded log prefixes; original local files remain untouched.
        with log.open('rb') as source:
            data = source.read(1024 * 1024)
        if log.stat().st_size > len(data):
            value['omissions'].append(f'{log.name}: log prefix only (1 MiB cap).')
        if used + len(data) <= EVIDENCE_LIMIT:
            (full / 'logs').mkdir(exist_ok=True)
            (full / 'logs' / log.name).write_bytes(data)
            used += len(data)
        else:
            value['omissions'].append(f'{log.name}: omitted by aggregate evidence cap.')
    output = root / 'publication'
    output.mkdir(exist_ok=True)
    try:
        raw = encode(value)
    except ValueError as error:
        # Preserve a bounded failure, never partial observations presented as success.
        for entry in value['suites']:
            ids = [record.get('publication', {}).get('manifest', {}).get('run_id', 'unknown')
                   for record in entry['records']]
            value['omissions'].append(f"{entry['package']}: publication history omitted ({', '.join(str(i)[:128] for i in ids[:4]) or 'no run'}): {str(error)[:256]}")
            entry['records'] = []
            entry['failure'] = failure('projection', 'Publication validation/aggregate cap failed; original history unavailable in compact transport.')
        raw = encode(value)
    (output / 'publication.json').write_bytes(raw)
    summary = [f'## Native benchmark: {platform} ({profile})',
               f"Run {identity['run_id']}, attempt {identity['run_attempt']}; candidate `{value['candidate_sha']}`; previous `{value['previous_sha']}`.",
               'Requested retention: 90 days. Effective expiry: pending GitHub API confirmation.',
               'Shared hosted runners: timing is advisory; no CPU affinity or power controls claimed.',
               '| Suite | Outcome |', '| --- | --- |']
    for entry in value['suites']:
        outcome = entry['failure']['stage'] + ' failure' if entry['failure'] else 'complete'
        summary.append(f"| {entry['package']} | {outcome} |")
    summary.extend(html.escape(item) for item in value['omissions'])
    # Candidate-rendered Markdown is local evidence only, never publisher input.
    for package in PACKAGES:
        report = root / 'logs' / f'{package}-run.md'
        if report.is_file():
            with report.open('rb') as source:
                fragment = source.read(128 * 1024)
            summary.append(fragment.decode('utf-8', errors='replace'))
            if report.stat().st_size > len(fragment):
                summary.append(f'{package}: job summary truncated at 128 KiB; full report in evidence logs.')
    (root / 'summary.md').write_text('\n'.join(summary) + '\n')
    # Small structured status remains available even if the full evidence upload fails.
    (full / 'publication.json').write_bytes(raw)
    return int(any(entry['failure'] for entry in value['suites']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--profile', choices=('full', 'smoke'), required=True)
    parser.add_argument('--platform', choices=('linux', 'macos'), required=True)
    parser.add_argument('--setup-stage', choices=STAGES, default='setup')
    args = parser.parse_args()
    return finalize(args.root.resolve(), args.cli.resolve(),
                    {'repository': os.environ['GITHUB_REPOSITORY'], 'run_id': os.environ['GITHUB_RUN_ID'],
                     'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT'])}, args.profile, args.platform, args.setup_stage)


if __name__ == '__main__':
    raise SystemExit(main())
