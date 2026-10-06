"""Feature selection must not miss members or hide a failed Cargo command."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'check_features.py'


class FeatureChecks(unittest.TestCase):
    def run_check(self, member_features, dependency_features=None, failure=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cargo = root / 'cargo'
            cargo.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with Path(os.environ['CALLS']).open('a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'metadata':
    print(os.environ['METADATA'])
else:
    sys.exit(int(os.environ['FAILURE']))
''')
            cargo.chmod(0o755)
            metadata = {'workspace_members': ['member'], 'packages': [
                {'id': 'member', 'name': 'example', 'features': member_features},
                {'id': 'dependency', 'name': 'dependency', 'features': dependency_features or {}},
            ]}
            env = dict(os.environ, PATH=f'{root}{os.pathsep}{os.environ["PATH"]}',
                       CALLS=str(root / 'calls'), METADATA=json.dumps(metadata), FAILURE=str(failure))
            result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()] if (root / 'calls').exists() else []
            return result, calls

    def test_featureless_workspace(self):
        result, calls = self.run_check({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('none', result.stdout)
        self.assertEqual(calls, [['metadata', '--locked', '--no-deps', '--format-version', '1']])

    def test_member_features_trigger_full_tests(self):
        result, calls = self.run_check({'optional': []})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('example', result.stdout)
        self.assertEqual(calls[1:], [
            ['nextest', 'run', '--locked', '--workspace', '--all-features', '--profile', 'ci'],
            ['test', '--locked', '--workspace', '--all-features', '--doc'],
        ])

    def test_dependency_features_do_not_trigger_full_tests(self):
        result, calls = self.run_check({}, {'default': ['optional']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_child_failure_is_preserved(self):
        result, calls = self.run_check({'optional': []}, failure=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
