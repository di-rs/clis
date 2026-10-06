"""Policy validation rejects missing inheritance/license and Cargo failures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'check_workspace.py'


class WorkspaceChecks(unittest.TestCase):
    def run_check(self, *, lint=True, license=True, cargo_failure=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'member').mkdir()
            (root/'Cargo.toml').write_text('[workspace]\nmembers=["member"]\n[workspace.package]\nlicense="MIT"\n[workspace.lints.rust]\nunsafe_code="forbid"\n')
            manifest='[package]\nname="example"\nversion="0.1.0"\n'
            if license:
                manifest+='license.workspace=true\n'
            if lint:
                manifest+='[lints]\nworkspace=true\n'
            (root/'member/Cargo.toml').write_text(manifest)
            metadata={'workspace_members':['example'], 'packages':[{
                'id':'example','name':'example','manifest_path':str(root/'member/Cargo.toml'),
                'license':'MIT' if license else None}]}
            cargo=root/'cargo'
            cargo.write_text('#!/usr/bin/env python3\nimport os,sys\nprint(os.environ["METADATA"])\nsys.exit(int(os.environ["FAILURE"]))\n')
            cargo.chmod(0o755)
            env=dict(os.environ, PATH=f'{root}{os.pathsep}{os.environ["PATH"]}', METADATA=json.dumps(metadata),FAILURE=str(cargo_failure))
            return subprocess.run([sys.executable,str(SCRIPT)],cwd=root,env=env,capture_output=True,text=True)

    def test_missing_lint_inheritance(self):
        result=self.run_check(lint=False)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('example',result.stderr)
        self.assertIn('lints.workspace',result.stderr)

    def test_missing_license(self):
        result=self.run_check(license=False)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('example',result.stderr)
        self.assertIn('license',result.stderr)

    def test_inherited_license(self):
        result=self.run_check()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('1 members',result.stdout)

    def test_metadata_failure_is_preserved(self):
        result=self.run_check(cargo_failure=19)
        self.assertEqual(result.returncode,19,result.stderr)


if __name__=='__main__':
    unittest.main()
