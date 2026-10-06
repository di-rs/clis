import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'locked-cargo.sh'


class LockedCargo(unittest.TestCase):
    def test_build_is_locked_and_failure_propagates(self):
        with tempfile.TemporaryDirectory() as directory:
            cargo=Path(directory)/'cargo-real'
            cargo.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(17)\n')
            cargo.chmod(0o755)
            result=subprocess.run(['bash',str(SCRIPT),'build','--manifest-path','fuzz/Cargo.toml'],
                                  env=dict(os.environ,CLIS_REAL_CARGO=str(cargo)),capture_output=True,text=True)
            self.assertEqual(result.returncode,17,result.stderr)
            self.assertEqual(json.loads(result.stdout),['build','--manifest-path','fuzz/Cargo.toml','--locked'])


if __name__=='__main__':
    unittest.main()
