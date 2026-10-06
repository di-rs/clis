"""Prebuilt cargo-fuzz must use rustc's host, not its own compiled-in target."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FuzzRunnerChecks(unittest.TestCase):
    def test_native_target_reaches_build_replay_and_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scripts').mkdir()
            (root/'bin').mkdir()
            (root/'fuzz/corpus/tail_bytes').mkdir(parents=True)
            (root/'fuzz/corpus/tail_bytes/seed').write_bytes(b'\0')
            for name in ['fuzz.sh', 'locked-cargo.sh']:
                shutil.copyfile(ROOT/'scripts'/name, root/'scripts'/name)
            (root/'Cargo.lock').write_text('root lock')
            (root/'fuzz/Cargo.lock').write_text('fuzz lock')
            cargo = root/'bin/cargo'
            cargo.write_text('#!/usr/bin/env python3\nimport json, os, sys\nwith open(os.environ["CALLS"], "a") as log: log.write(json.dumps(sys.argv[1:])+"\\n")\n')
            cargo.chmod(0o755)
            rustc = root/'bin/rustc'
            rustc.write_text('#!/bin/sh\nprintf "rustc test\\nhost: x86_64-unknown-linux-gnu\\n"\n')
            rustc.chmod(0o755)
            calls = root/'calls'
            result = subprocess.run(['bash', str(root/'scripts/fuzz.sh'), 'tail_bytes', '1'],
                                    env=dict(os.environ, PATH=str(root/'bin')+os.pathsep+os.environ['PATH'], CALLS=str(calls)),
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            builds = [json.loads(line) for line in calls.read_text().splitlines() if json.loads(line)[:1] == ['fuzz']]
            self.assertEqual(len(builds), 3)
            for argv in builds:
                self.assertIn('--target', argv)
                self.assertEqual(argv[argv.index('--target')+1], 'x86_64-unknown-linux-gnu')


if __name__ == '__main__':
    unittest.main()
