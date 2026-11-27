"""The waiting solver checks on every job that newton's sources are the ones it started with
(solver/live_session_worker.engine_identity). The sources are read once per process; a later job compares each file's
modified time and size only, and reads them all again when anything differs."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOLVER = Path(__file__).resolve().parents[1] / 'solver'


def _load():
    """live_session_worker with its solver-only imports stubbed (no newton / warp needed)."""
    stubs = {name: types.ModuleType(name) for name in ('process_tree', 'prepared_live', 'preload_config',
                                                        'lifecycle_trace', 'job_cancel', 'runtime_guard')}
    stubs['runtime_guard'].verify_actual_environment = lambda: None
    stubs['process_tree'].join_owned_job = lambda *a, **k: None
    stubs['lifecycle_trace'].mark = lambda *a, **k: None
    stubs['job_cancel'].check = lambda: None
    stubs['job_cancel'].CanceledJob = type('CanceledJob', (Exception,), {})
    with patch.dict(sys.modules, stubs):
        ns = {'__name__': 'live_session_worker_under_test', '__file__': str(SOLVER / 'live_session_worker.py')}
        exec(compile((SOLVER / 'live_session_worker.py').read_text(encoding='utf8'),
                     str(SOLVER / 'live_session_worker.py'), 'exec'), ns)
    return ns


class NewtonDigestCache(unittest.TestCase):
    def setUp(self):
        self.ns = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'sub').mkdir()
        (self.root / 'a.py').write_text('a = 1\n')
        (self.root / 'sub' / 'b.py').write_text('b = 2\n')
        (self.root / 'notes.txt').write_text('not a source')

    def tearDown(self):
        self.tmp.cleanup()

    def digest(self):
        reads = []
        real = Path.read_bytes

        def counted(path):
            reads.append(path.name)
            return real(path)
        with patch.object(Path, 'read_bytes', counted):
            return self.ns['_newton_digest'](self.root), reads

    def test_second_job_reads_nothing(self):
        first, reads = self.digest()
        self.assertEqual(sorted(reads), ['a.py', 'b.py'])
        again, reads = self.digest()
        self.assertEqual(again, first)
        self.assertEqual(reads, [])

    def test_changed_file_is_noticed(self):
        first, _ = self.digest()
        path = self.root / 'sub' / 'b.py'
        path.write_text('b = 3\n')                     # same size: the modified time tells
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        changed, reads = self.digest()
        self.assertNotEqual(changed, first)
        self.assertEqual(sorted(reads), ['a.py', 'b.py'])

    def test_added_and_removed_files_are_noticed(self):
        first, _ = self.digest()
        (self.root / 'sub' / 'c.py').write_text('c = 4\n')
        added, _ = self.digest()
        self.assertNotEqual(added, first)
        (self.root / 'sub' / 'c.py').unlink()
        back, _ = self.digest()
        self.assertEqual(back, first)


if __name__ == '__main__':
    unittest.main()
