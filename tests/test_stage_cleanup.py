"""Actual stage runner must close registered resources before its os._exit."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class StageCleanupTests(unittest.TestCase):
    def run_runner(self, body, expected_code):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            for name in ('run_stages.py', 'lifecycle_trace.py'):
                shutil.copy2(root / 'solver' / name, work / name)
            stage = work / 'stage'
            stage.mkdir()
            marker = work / 'closed.txt'
            prefix = '''import json,sys
from pathlib import Path
marker=Path(sys.argv[4]).parent/'closed.txt'
def close(label):
    with marker.open('a') as stream:stream.write(label+'\\n')
_AC9_CLEANUP(lambda:close('first'))
_AC9_CLEANUP(lambda:close('second'))
'''
            (work / 'newton_vbd.py').write_text(prefix + body, encoding='utf8')
            env = dict(os.environ, AC9_LIFECYCLE_DIR='0')
            process = subprocess.run([sys.executable, str(work / 'run_stages.py'), 'scene', 'body', str(stage)],
                                     capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(process.returncode, expected_code, process.stdout + process.stderr)
            self.assertEqual(marker.read_text().splitlines(), ['second', 'first'])

    def test_normal_stage_closes_in_reverse_order(self):
        self.run_runner("(Path(sys.argv[4])/'run_log.json').write_text('{}')\n", 0)

    def test_top_level_failure_still_closes(self):
        self.run_runner("raise RuntimeError('Injected top-level stage failure')\n", 1)

    def test_bad_cleanup_does_not_skip_other_resources(self):
        self.run_runner("_AC9_CLEANUP(lambda:(_ for _ in ()).throw(RuntimeError('close failed')))\n", 1)


if __name__ == '__main__':
    unittest.main()
