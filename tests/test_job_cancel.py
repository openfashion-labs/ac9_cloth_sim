"""Cancellation belongs to one job and cannot leak to the next one."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'solver'))
import job_cancel as cancel

class JobCancellation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);cancel.clear();self.addCleanup(cancel.clear)
        env=patch.dict(os.environ,{'AC9_LIFECYCLE_DIR':'0'});env.start();self.addCleanup(env.stop)
    def test_no_active_job_cannot_cancel_an_unrelated_run(self):
        (self.root/'stop').touch();cancel.check()
        self.assertFalse((self.root/'stop_seen').exists())
    def test_stop_acknowledged_before_raising(self):
        cancel.activate(self.root);(self.root/'stop').touch()
        with self.assertRaises(cancel.CanceledJob):cancel.check()
        self.assertTrue((self.root/'stop_seen').exists())
    def test_no_stop_is_noop(self):
        cancel.activate(self.root);cancel.check()
        self.assertFalse((self.root/'stop_seen').exists())
    def test_old_stop_cannot_cancel_next_job(self):
        old=self.root/'old';new=self.root/'new';old.mkdir();new.mkdir()
        cancel.activate(old);(old/'stop').touch()
        with self.assertRaises(cancel.CanceledJob):cancel.check()
        cancel.clear();cancel.activate(new);cancel.check()
        self.assertFalse((new/'stop_seen').exists())
    def test_nested_owner_refused(self):
        cancel.activate(self.root)
        with self.assertRaises(RuntimeError):cancel.activate(self.root/'other')
    def test_ack_write_failure_does_not_ignore_stop(self):
        cancel.activate(self.root);(self.root/'stop').touch()
        with patch.object(Path,'touch',side_effect=OSError('locked')):
            with self.assertRaises(cancel.CanceledJob):cancel.check()
if __name__=='__main__':unittest.main()
