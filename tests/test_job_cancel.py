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
    def _aged(self):
        path=self.root/'session_setup.json';os.utime(path,(1000,1000));return path
    def test_preparing_check_touches_the_setup_file(self):
        # the add-on's stuck check watches this file's time: a long first plan must keep it moving
        cancel.activate(self.root);path=self._aged();text=path.read_text(encoding='utf8')
        with patch.object(cancel,'BEAT_S',0.0):cancel.check()
        self.assertGreater(path.stat().st_mtime,1000)
        self.assertEqual(path.read_text(encoding='utf8'),text)
    def test_no_touch_once_the_loop_runs(self):
        cancel.activate(self.root);cancel.loop_ready();path=self._aged()
        with patch.object(cancel,'BEAT_S',0.0):cancel.check()
        self.assertEqual(path.stat().st_mtime,1000)
    def test_touch_is_rate_limited(self):
        cancel.activate(self.root);path=self._aged()
        cancel.check()      # right after activate: inside BEAT_S
        self.assertEqual(path.stat().st_mtime,1000)
    def test_touch_failure_is_ignored(self):
        cancel.activate(self.root)
        with patch.object(cancel,'BEAT_S',0.0),patch.object(os,'utime',side_effect=OSError('locked')):cancel.check()
    def _moves_within(self,path,seconds):
        import time
        end=time.time()+seconds
        while time.time()<end:
            if path.stat().st_mtime>1000:return True
            time.sleep(0.02)
        return False
    def test_busy_step_keeps_the_setup_file_moving(self):
        # Newton's finalize calls nothing back; a thread beats for it until busy_end
        cancel.activate(self.root);path=self._aged()
        with patch.object(cancel,'BEAT_S',0.05):
            cancel.busy_begin('finalize')
            try:self.assertTrue(self._moves_within(path,2.0))
            finally:cancel.busy_end()
            os.utime(path,(1000,1000))
            self.assertFalse(self._moves_within(path,0.3))
    def test_busy_beat_stops_when_the_loop_starts(self):
        cancel.activate(self.root)
        with patch.object(cancel,'BEAT_S',0.05):
            cancel.busy_begin('finalize');cancel.loop_ready();path=self._aged()
            self.assertFalse(self._moves_within(path,0.3))
    def test_busy_without_a_session_does_nothing(self):
        cancel.busy_begin('finalize');cancel.busy_end()
        self.assertFalse((self.root/'session_setup.json').exists())
    def test_finalize_statement_kept_for_the_resident_runner(self):
        # resident_runner finds the model's finalize by its exact text
        src=(Path(__file__).resolve().parents[1]/'solver'/'newton_vbd.py').read_text(encoding='utf-8')
        self.assertIn('\nmodel = builder.finalize()\n',src)
        self.assertIn("_AC9_BUSY_BEGIN('finalize')",src)
if __name__=='__main__':unittest.main()
