"""Exercise the shell adapter with an explicit fake external CLI, never timing."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class NativeTests(unittest.TestCase):
    def test_run_keeps_failures_and_continues_other_suites_without_biggie_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'status').mkdir()
            (root / 'target').mkdir()
            for package in ('biggie', 'tailr', 'mkdirr'):
                (root / 'status' / (package + '.json')).write_text('null\n')
            cli = root / 'cli'
            cli.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with (Path(os.environ['BENCH_ROOT']) / 'calls.jsonl').open('a') as output:
    output.write(json.dumps(args) + '\\n')
sys.exit(7 if args[args.index('-p') + 1] == 'tailr' else 0)
''')
            cli.chmod(0o755)
            env = dict(os.environ, BENCH_CI=str(Path(__file__).parent.resolve()), BENCH_ROOT=str(root), BENCH_CLI=str(cli), CANDIDATE_SHA='a' * 40,
                       PREVIOUS_SHA='b' * 40, RUSTUP_TOOLCHAIN='nightly-2026-10-04',
                       MEASUREMENT_PROFILE='smoke', HYPERFINE='/explicit/hyperfine',
                       GNU_TAIL='/explicit/tail', GNU_MKDIR='/explicit/mkdir')
            result = subprocess.run(['bash', str(Path(__file__).parent.resolve() / 'native.sh'), 'run'], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual([args[args.index('-p') + 1] for args in calls], ['biggie', 'tailr', 'mkdirr'])
            self.assertNotIn('-x', calls[0])
            self.assertEqual(calls[1][-2:], ['-x', '/explicit/tail'])
            self.assertEqual(calls[2][-2:], ['-x', '/explicit/mkdir'])
            for args in calls:
                self.assertEqual(args[args.index('-r') + 1], 'a' * 40)
                self.assertEqual(args[args.index('-b') + 1], 'b' * 40)
            self.assertEqual(json.loads((root / 'status/tailr.json').read_text())['exit_code'], 7)
            self.assertIsNone(json.loads((root / 'status/mkdirr.json').read_text()))
