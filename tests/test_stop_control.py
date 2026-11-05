"""Stop control must never wait inside the UI timer, even after its deadline."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solver.stop_control import PendingStop


class Process:
    pid = 123
    code = None
    kills = 0

    def poll(self):
        return self.code

    def kill(self):
        self.kills += 1

    def wait(self, *args, **kwargs):
        raise AssertionError('the UI must not wait')


class StopControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'AC9_LIFECYCLE_DIR': '0'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_waits_for_exit_without_blocking_or_killing(self):
        p = Process()
        stop = PendingStop(p, [self.temp.name])
        self.assertTrue((Path(self.temp.name) / 'stop').exists())
        self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 0)
        p.code = 0
        self.assertTrue(stop.poll())
        self.assertTrue(stop.poll())
        self.assertEqual(p.kills, 0)

    def test_timeout_sends_exactly_one_kill_then_polls_exit(self):
        p = Process()
        with patch('solver.stop_control.time.monotonic', return_value=10):
            stop = PendingStop(p, [self.temp.name], timeout=60)
        (Path(self.temp.name) / 'progress.txt').write_text('5 70')      # a frame ended: the solver read the stop
        with patch('solver.stop_control.time.monotonic', return_value=69):
            self.assertFalse(stop.poll())
            self.assertEqual(p.kills, 0)
        with patch('solver.stop_control.time.monotonic', return_value=70):
            self.assertFalse(stop.poll())
            self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 1)
        p.code = 1
        self.assertTrue(stop.poll())

    def test_missing_stage_does_not_prevent_stopping_other_stages(self):
        p = Process()
        stop = PendingStop(p, [str(Path(self.temp.name)/'missing'), self.temp.name])
        self.assertTrue((Path(self.temp.name)/'stop').exists())
        self.assertFalse(stop.poll())

    def test_stuck_solver_is_killed_after_the_grace_not_the_deadline(self):
        p = Process()
        (Path(self.temp.name) / 'progress.txt').write_text('23 70')
        with patch('solver.stop_control.time.monotonic', return_value=10):
            stop = PendingStop(p, [self.temp.name], timeout=60, frame_s=0.25)
        self.assertEqual(stop.grace, 5.0)
        with patch('solver.stop_control.time.monotonic', return_value=14.9):
            self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 0)
        with patch('solver.stop_control.time.monotonic', return_value=15):
            self.assertFalse(stop.poll())
            self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 1)

    def test_slow_frames_get_three_frames_of_grace(self):
        p = Process()
        with patch('solver.stop_control.time.monotonic', return_value=0):
            stop = PendingStop(p, [self.temp.name], timeout=60, frame_s=8.0)
        self.assertEqual(stop.grace, 24.0)
        with patch('solver.stop_control.time.monotonic', return_value=23):
            self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 0)

    def test_between_stages_waits_for_the_next_stage_setup(self):
        p = Process()
        with patch('solver.stop_control.time.monotonic', return_value=0):
            stop = PendingStop(p, [self.temp.name], timeout=60, frame_s=0.25, min_s=40.0)
        with patch('solver.stop_control.time.monotonic', return_value=39):
            self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 0)
        with patch('solver.stop_control.time.monotonic', return_value=40):
            self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 1)

    def test_frame_printed_while_progress_file_refused_counts(self):
        p = Process()
        out = Path(self.temp.name) / 'stdout.txt'
        out.write_text('progress 22 70' + chr(10) + 'progress 23 70' + chr(10))
        with patch('solver.stop_control.time.monotonic', return_value=0):
            stop = PendingStop(p, [self.temp.name], timeout=60)
        with open(out, 'a') as f:
            f.write('progress 24 70' + chr(10))
        with patch('solver.stop_control.time.monotonic', return_value=30):
            self.assertFalse(stop.poll())
        self.assertTrue(stop.moved)
        self.assertEqual(p.kills, 0)

    def test_stop_seen_file_gives_the_full_timeout(self):
        p = Process()
        with patch('solver.stop_control.time.monotonic', return_value=0):
            stop = PendingStop(p, [self.temp.name], timeout=60)
        (Path(self.temp.name) / 'stop_seen').write_text('')
        with patch('solver.stop_control.time.monotonic', return_value=30):
            self.assertFalse(stop.poll())
        self.assertTrue(stop.moved)
        self.assertEqual(p.kills, 0)

    def test_old_output_lines_do_not_count(self):
        p = Process()
        (Path(self.temp.name) / 'stdout.txt').write_text('progress 23 70' + chr(10))
        with patch('solver.stop_control.time.monotonic', return_value=0):
            stop = PendingStop(p, [self.temp.name], timeout=60)
        with patch('solver.stop_control.time.monotonic', return_value=6):
            self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 1)


class StopFailureTests(unittest.TestCase):
    setUp = StopControlTests.setUp
    def test_denied_kill_retains_owner_until_observed_exit(self):
        class Denied(Process):
            def kill(self):
                self.kills += 1
                raise PermissionError('fixture kill denied')
        p = Denied()
        stop = PendingStop(p, [self.temp.name], timeout=0)
        self.assertFalse(stop.poll())
        self.assertIn('denied', stop.error)
        self.assertFalse(stop.poll())
        self.assertEqual(p.kills, 1)
        p.code = 0
        self.assertTrue(stop.poll())

    def test_exit_racing_kill_is_reaped(self):
        class Exited(Process):
            def kill(self):
                self.code = 0
                raise PermissionError('already exited')
        stop = PendingStop(Exited(), [self.temp.name], timeout=0)
        self.assertTrue(stop.poll())

    def test_poll_failure_keeps_process(self):
        class Failed(Process):
            def poll(self):
                raise OSError('fixture poll denied')
        p = Failed()
        stop = PendingStop(p, [self.temp.name])
        self.assertFalse(stop.poll())
        self.assertIs(stop.proc, p)
        self.assertIn('poll denied', stop.error)

    def test_retired_owner_survives_module_reload(self):
        import importlib
        import solver.stop_control as control
        p = Process()
        stop = control.retire(p, [self.temp.name])
        self.assertTrue(control.retired_running())
        control = importlib.reload(control)
        self.assertIn(stop, control._RETIRED_STOPS)
        self.assertTrue(control.retired_running())
        p.code = 0
        self.assertIsNone(control.reap_retired())


if __name__ == '__main__':
    unittest.main()
