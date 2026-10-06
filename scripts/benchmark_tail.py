#!/usr/bin/env python3
"""Check both Rust revisions against GNU tail before collecting any timings."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

from reference_check import check_cases, run_command

TIMING_TIMEOUT_SECONDS = 600


def fingerprint(path: Path) -> dict:
    with path.open('rb') as stream:
        return {'path': str(path.resolve()), 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest(),
                'bytes': path.stat().st_size}


def timed_command(binary: Path, case: dict, data: Path) -> str:
    argv = [str(binary), *[str(data) if arg == 'input.txt' else arg for arg in case['argv']]]
    command = shlex.join(argv) + ' > /dev/null'
    if case['input'] == 'stdin':
        pipeline = shlex.join(['cat', str(data)]) + ' | ' + command
        command = shlex.join(['bash', '--noprofile', '--norc', '-o', 'pipefail', '-c', pipeline])
    return command


def benchmark(args) -> int:
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError(f'Benchmark output directory must be empty: {out}')
    report = {'success': False, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'revisions': {'candidate': args.candidate_revision, 'baseline': args.baseline_revision,
                            'generator': args.generator_revision}, 'samples': []}
    try:
        for name in ['reference', 'candidate', 'baseline', 'generator']:
            path = getattr(args, name)
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError(f'An absolute executable path is required for {name}: {path}')
        report['binaries'] = {name: fingerprint(getattr(args, name))
                              for name in ['reference', 'candidate', 'baseline', 'generator']}
        timer = shutil.which(str(args.hyperfine))
        if not timer:
            raise ValueError('Hyperfine is unavailable')
        report['environment'] = {'platform': platform.platform(), 'cpu': platform.processor(),
                                 'logical_cpus': os.cpu_count(), 'LC_ALL': 'C', 'TZ': 'UTC',
                                 'rustc': subprocess.check_output(['rustc', '-Vv'], text=True),
                                 'hyperfine': subprocess.check_output([timer, '--version'], text=True),
                                 'warmups': 3, 'runs': 20, 'cache': 'warm; no cache eviction',
                                 'stdout': '/dev/null', 'build': 'release; identical compiler/flags required',
                                 'memory': 'not measured'}
        report['lockfiles'] = {}
        for name in ['candidate', 'baseline', 'generator']:
            path = getattr(args, name+'_lockfile')
            report['lockfiles'][name] = fingerprint(path) if path else {'status': 'not supplied'}
        data = out/'input.txt'
        generate = [str(args.generator), '--seed', '42', '--lines', '1000' if args.smoke else '1000000',
                    '--words-per-line', '4', '--word-length', '8', '--line-ending', 'lf', str(data)]
        generated = subprocess.run(generate, capture_output=True, timeout=120, check=True)
        (out/'generator.stdout').write_bytes(generated.stdout)
        (out/'generator.stderr').write_bytes(generated.stderr)
        report['input'] = {**fingerprint(data), 'generator_argv': generate}
        cases = json.loads(args.cases.read_text())
        checks = []
        for case in cases:
            if case['input'] not in ['file', 'stdin']:
                raise ValueError('Benchmark input must be file or stdin')
            check = {'id': case['id'], 'argv': case['argv'], 'expected_status': 0, 'timeout_seconds': 30}
            if case['input'] == 'file':
                check['files'] = {'input.txt': {'source': str(data)}}
            else:
                check['stdin_source'] = str(data)
            checks.append(check)
        # All cases for both revisions must pass before launching Hyperfine at all.
        for name in ['candidate', 'baseline']:
            checked = check_cases(args.reference, getattr(args, name), checks, out/name, out)
            if not checked['success']:
                raise ValueError(f'{name} failed correctness preflight; timing was not started')
        for case in cases:
            destination = out/(case['id']+'.json')
            command = [timer, '--warmup', '3', '--runs', '20', '--export-json', str(destination)]
            for label, binary in [('reference', args.reference), ('previous', args.baseline), ('candidate', args.candidate)]:
                command += ['--command-name', label, timed_command(binary, case, data)]
            timed = run_command(command, out, None, TIMING_TIMEOUT_SECONDS, out/(case['id']+'-timing'))
            if timed['timed_out']:
                raise ValueError(f"Benchmark {case['id']} timed out; process group terminated")
            if timed['status'] != 0:
                raise ValueError(f"Benchmark {case['id']} failed with status {timed['status']}")
            samples = json.loads(destination.read_text())
            results = {result['command']: result for result in samples['results']}
            ratios = {name: results['candidate']['mean']/results[name]['mean'] for name in ['reference', 'previous']}
            report['samples'].append({'id': case['id'], 'argv': command, **samples, 'candidate_time_ratio': ratios,
                                      'interpretation': 'Exploratory shared-host timing; repeat noisy differences on a controlled host.'})
        report['success'] = True
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError, subprocess.SubprocessError) as error:
        report['error'] = str(error)
        print(error, file=sys.stderr)
    (out/'benchmark.json').write_text(json.dumps(report, indent=2)+'\n')
    return 0 if report['success'] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['candidate', 'baseline', 'reference', 'generator', 'cases', 'output-dir']:
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ['candidate', 'baseline', 'generator']:
        parser.add_argument('--'+name+'-revision', required=True)
        parser.add_argument('--'+name+'-lockfile', type=Path)
    parser.add_argument('--hyperfine', type=Path, default=Path('hyperfine'))
    parser.add_argument('--smoke', action='store_true', help='Generate 1000 records instead of one million')
    args = parser.parse_args()
    try:
        return benchmark(args)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
