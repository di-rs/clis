#!/usr/bin/env python3
"""Compare scoped tail cases against an explicitly identified GNU executable."""
import argparse
import filecmp
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile


def run_command(argv: list[str], cwd: Path, stdin: bytes | None, timeout: float,
                prefix: Path) -> dict:
    """Retain raw streams on disk and terminate the entire process group on timeout."""
    with prefix.with_suffix('.stdout').open('wb') as out, prefix.with_suffix('.stderr').open('wb') as err:
        process = subprocess.Popen(argv, cwd=cwd, env=dict(os.environ, LC_ALL='C', TZ='UTC'),
                                   stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        try:
            process.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    return {'argv': argv, 'status': process.returncode, 'timed_out': timed_out}


def safe_relative(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError(f'Unsafe fixture path: {name}')
    return path


def check_cases(reference: Path, candidate: Path, cases: list[dict],
                output_dir: Path, case_root: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError(f'Evidence directory must be empty: {output_dir}')
    report = {'platform': platform.platform(), 'environment': {'LC_ALL': 'C', 'TZ': 'UTC'},
              'reference': str(reference), 'candidate': str(candidate), 'cases': []}
    try:
        for executable in [reference, candidate]:
            if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
                raise ValueError(f'An absolute executable path is required: {executable}')
        version = run_command([str(reference), '--version'], output_dir, None, 5, output_dir/'version')
        identity = (output_dir/'version.stdout').read_text(errors='replace')
        if version['status'] != 0 or version['timed_out'] or not re.match(r'^tail \(GNU coreutils\) \d+\.\d+', identity):
            raise ValueError('Reference must identify itself as GNU coreutils tail')
        report['reference_version'] = identity
        if not cases:
            raise ValueError('At least one reference case is required')
        ids = set()
        for case in cases:
            case_id = case['id']
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', case_id) or case_id in ids:
                raise ValueError(f'Invalid or duplicate case ID: {case_id}')
            ids.add(case_id)
            directory = output_dir/case_id
            directory.mkdir()
            timeout = float(case.get('timeout_seconds', 5))
            if not 0 < timeout <= 60:
                raise ValueError('Case timeout must be between 0 and 60 seconds')
            with tempfile.TemporaryDirectory(prefix='clis-reference-') as temporary:
                sandbox = Path(temporary)
                inputs = {}
                for name, fixture in case.get('files', {}).items():
                    target = sandbox/safe_relative(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if 'hex' in fixture:
                        target.write_bytes(bytes.fromhex(fixture['hex']))
                    else:
                        shutil.copyfile(case_root/fixture['source'], target)
                    with target.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    inputs[name] = {'sha256': digest, 'bytes': target.stat().st_size}
                stdin = bytes.fromhex(case['stdin_hex']) if 'stdin_hex' in case else None
                if 'stdin_source' in case:
                    stdin = (case_root/case['stdin_source']).read_bytes()
                argv = case['argv']
                if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
                    raise ValueError('argv must be an array of strings')
                expected_status = case['expected_status']
                ref = run_command([str(reference), *argv], sandbox, stdin, timeout, directory/'reference')
                cand = run_command([str(candidate), *argv], sandbox, stdin, timeout, directory/'candidate')
                matches = (not ref['timed_out'] and not cand['timed_out'] and
                           ref['status'] == cand['status'] == expected_status and
                           filecmp.cmp(directory/'reference.stdout', directory/'candidate.stdout', shallow=False) and
                           filecmp.cmp(directory/'reference.stderr', directory/'candidate.stderr', shallow=False))
                report['cases'].append({'id': case_id, 'reference': ref, 'candidate': cand,
                                        'expected_status': expected_status, 'matches': matches,
                                        'files': inputs, 'stdin_sha256': hashlib.sha256(stdin).hexdigest() if stdin is not None else None,
                                        'timeout_seconds': timeout})
        report['success'] = all(case['matches'] for case in report['cases'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        report['success'] = False
        report['error'] = str(error)
    (output_dir/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['reference', 'candidate', 'cases', 'output-dir']:
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = check_cases(args.reference, args.candidate, json.loads(args.cases.read_text()),
                             args.output_dir.resolve(), args.cases.resolve().parent)
        print(f'Reference cases: {sum(case["matches"] for case in report["cases"])}/{len(report["cases"])} match')
        if 'error' in report:
            print(report['error'], file=sys.stderr)
        return 0 if report['success'] else 1
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
