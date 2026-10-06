"""No timing may begin before every candidate/baseline correctness check passes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from test_reference_check import executable

SCRIPT = Path(__file__).resolve().parents[1] / 'benchmark_tail.py'


class BenchmarkChecks(unittest.TestCase):
    def run_benchmark(self, body='sys.stdout.buffer.write(b"tail\\n")', missing_reference=False, hanging_timer=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            reference=executable(root/'gnu-tail','sys.stdout.buffer.write(b"tail\\n")')
            candidate=executable(root/'tailr',body)
            baseline=executable(root/'baseline','sys.stdout.buffer.write(b"tail\\n")')
            generator=executable(root/'biggie','from pathlib import Path; Path(sys.argv[-1]).write_bytes(b"first\\ntail\\n")')
            timer=executable(root/'hyperfine','''import os, json
from pathlib import Path
Path(os.environ['TIMER_CALLED']).write_text('called')
out=Path(sys.argv[sys.argv.index('--export-json')+1])
out.write_text(json.dumps({'results':[{'command':name,'mean':1.0,'median':1.0,'stddev':0.1,'times':[0.9,1.0,1.1]} for name in ['reference','previous','candidate']]}))''',identity='hyperfine 1.20.0')
            if hanging_timer:
                timer=executable(root/'hyperfine', '''import os
from pathlib import Path
subprocess.Popen([sys.executable, '-c', 'import time; from pathlib import Path; time.sleep(0.4); Path('+repr(os.environ['CHILD_MARKER'])+').write_text("survived")'])
time.sleep(2)
''',identity='hyperfine 1.20.0')
            cases=root/'cases.json';cases.write_text(json.dumps([{'id':'last-line','argv':['-n','1','input.txt'],'input':'file'}]))
            out=root/'results'
            result=subprocess.run([sys.executable,'-c', 'import sys; sys.path.insert(0, '+repr(str(SCRIPT.parent))+'); import benchmark_tail; benchmark_tail.TIMING_TIMEOUT_SECONDS='+('0.1' if hanging_timer else '600')+'; sys.exit(benchmark_tail.main())','--reference',str(reference if not missing_reference else root/'missing'),
                                   '--candidate',str(candidate),'--baseline',str(baseline),'--generator',str(generator),
                                   '--hyperfine',str(timer),'--cases',str(cases),'--output-dir',str(out),
                                   '--candidate-revision','candidate-sha','--baseline-revision','baseline-sha',
                                   '--generator-revision','generator-sha','--smoke'],
                                  env=dict(os.environ,TIMER_CALLED=str(root/'called'),CHILD_MARKER=str(root/'child-survived')),capture_output=True,text=True,timeout=15)
            if hanging_timer:
                time.sleep(0.6)
                self.assertFalse((root/'child-survived').exists(), 'timed-out benchmark child survived')
            report=json.loads((out/'benchmark.json').read_text()) if (out/'benchmark.json').exists() else {}
            return result, (root/'called').exists(), report

    def test_timeout_kills_timer_descendants(self):
        result,_,report=self.run_benchmark(hanging_timer=True)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('timed out',report['error'])

    def test_wrong_output_prevents_timing(self):
        result,called,_=self.run_benchmark(body='sys.stdout.buffer.write(b"wrong")')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(called)

    def test_failed_command_prevents_timing(self):
        result,called,_=self.run_benchmark(body='sys.stdout.buffer.write(b"tail\\n"); sys.exit(4)')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(called)

    def test_missing_reference_fails(self):
        result,called,_=self.run_benchmark(missing_reference=True)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(called)

    def test_baseline_identity_recorded(self):
        result,called,report=self.run_benchmark()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(called)
        self.assertEqual(report['revisions'],{'candidate':'candidate-sha','baseline':'baseline-sha','generator':'generator-sha'})
        self.assertEqual(report['samples'][0]['results'][2]['times'],[0.9,1.0,1.1])
        self.assertIn('sha256',report['input'])


if __name__=='__main__':
    unittest.main()
