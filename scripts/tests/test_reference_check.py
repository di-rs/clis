"""The live-reference gate compares raw process outcomes and kills hung children."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'reference_check.py'


def executable(path, body, identity='tail (GNU coreutils) 9.12'):
    path.write_text('#!/usr/bin/env python3\nimport sys, time, subprocess\n'
                    f'if "--version" in sys.argv:\n    print({identity!r})\n    sys.exit(0)\n'+body+'\n')
    path.chmod(0o755)
    return path


class ReferenceChecks(unittest.TestCase):
    def run_case(self, candidate='sys.stdout.buffer.write(b"\\0\\xff")',
                 reference='sys.stdout.buffer.write(b"\\0\\xff")',
                 identity='tail (GNU coreutils) 9.12', status=0, timeout=2):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ref=executable(root/'gnu-tail',reference,identity)
            cand=executable(root/'tailr',candidate)
            cases=root/'cases.json'
            cases.write_text(json.dumps([{'id':'case','argv':[], 'expected_status':status,
                                          'timeout_seconds':timeout}]))
            out=root/'results'
            result=subprocess.run([sys.executable,str(SCRIPT),'--reference',str(ref),
                                   '--candidate',str(cand),'--cases',str(cases),
                                   '--output-dir',str(out)],capture_output=True,text=True,timeout=10)
            report=json.loads((out/'report.json').read_text()) if (out/'report.json').exists() else {}
            raw=(out/'case/candidate.stdout').read_bytes() if (out/'case/candidate.stdout').exists() else None
            return result,report,raw

    def test_wrong_reference_rejected(self):
        result,report,_=self.run_case(identity='BSD tail')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('GNU',report['error'])

    def test_timeout_fails(self):
        result,report,_=self.run_case(candidate='subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); time.sleep(30)',timeout=0.1)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertTrue(report['cases'][0]['candidate']['timed_out'])

    def test_stdout_byte_mismatch(self):
        result,report,raw=self.run_case(candidate='sys.stdout.buffer.write(b"\\0\\xfe")')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(report['cases'][0]['matches'])
        self.assertEqual(raw,b'\0\xfe')

    def test_stderr_mismatch(self):
        result,report,_=self.run_case(candidate='sys.stdout.buffer.write(b"\\0\\xff"); sys.stderr.write("unexpected")')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(report['cases'][0]['matches'])

    def test_exit_status_mismatch(self):
        result,report,_=self.run_case(candidate='sys.stdout.buffer.write(b"\\0\\xff"); sys.exit(7)')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual(report['cases'][0]['candidate']['status'],7)

    def test_matching_expected_nonzero(self):
        result,report,raw=self.run_case(candidate='sys.stdout.buffer.write(b"\\0\\xff"); sys.exit(7)',
                                      reference='sys.stdout.buffer.write(b"\\0\\xff"); sys.exit(7)',status=7)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(report['cases'][0]['matches'])
        self.assertEqual(raw,b'\0\xff')

    def test_matching_unexpected_failure_does_not_pass(self):
        result,report,_=self.run_case(candidate='sys.exit(7)',reference='sys.exit(7)')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(report['cases'][0]['matches'])

    def test_missing_reference_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            cases=root/'cases.json';cases.write_text('[]')
            result=subprocess.run([sys.executable,str(SCRIPT),'--reference',str(root/'missing'),
                                   '--candidate',sys.executable,'--cases',str(cases),
                                   '--output-dir',str(root/'out')],capture_output=True,text=True)
            self.assertEqual(result.returncode,1,result.stderr)


if __name__=='__main__':
    unittest.main()
