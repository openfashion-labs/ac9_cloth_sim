"""CPU task ownership must survive errors without exposing live readers."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solver.fast_live.cpu_hinge_geometry import CpuHingeGeometry


class CpuGeometryOwnershipTests(unittest.TestCase):
    def test_small_input_is_lazy_and_close_is_final(self):
        owner = CpuHingeGeometry()
        result = owner.angles(np.zeros((0, 3)), np.zeros((0, 4), int), np.zeros(0))
        self.assertEqual(result.shape, (0,))
        self.assertIsNone(owner._executor)
        owner.close()
        owner.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            owner.angles(np.zeros((0, 3)), np.zeros((0, 4), int), np.zeros(0))

    def test_failed_task_waits_for_sibling_readers(self):
        owner = CpuHingeGeometry()
        owner.workers = 4
        ready, release, returned = threading.Event(), threading.Event(), threading.Event()
        lock = threading.Lock()
        started, finished, errors = [], [], []

        def task(q, edge, chart, policy):
            key = int(chart[0])
            with lock:
                started.append(key)
                if len(started) == 4:
                    ready.set()
            try:
                if not ready.wait(3):
                    raise RuntimeError('Task setup timeout')
                if key == 0:
                    raise ValueError('Injected geometry failure')
                if not release.wait(3):
                    raise RuntimeError('Sibling release timeout')
                return np.zeros(len(edge))
            finally:
                with lock:
                    finished.append(key)

        def evaluate():
            try:
                owner.angles(np.zeros((4, 3)), np.tile(np.arange(4), (32768, 1)),
                             np.arange(32768, dtype=float))
            except Exception as error:
                errors.append(error)
            finally:
                returned.set()

        with patch('solver.fast_live.cpu_hinge_geometry._task', task):
            thread = threading.Thread(target=evaluate)
            thread.start()
            try:
                self.assertTrue(ready.wait(3))
                self.assertFalse(returned.wait(.05))
            finally:
                release.set()
                thread.join(3)
                owner.close()
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(finished), 4)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)

    def test_threads_preserve_numpy_error_policy(self):
        owner = CpuHingeGeometry()
        owner.workers = 4
        original = np.geterr()
        try:
            with np.errstate(invalid='raise', divide='raise'):
                with self.assertRaises(FloatingPointError):
                    owner.angles(np.zeros((4, 3)), np.tile(np.arange(4), (32768, 1)),
                                 np.zeros(32768))
            self.assertEqual(np.geterr(), original)
        finally:
            owner.close()


if __name__ == '__main__':
    unittest.main()
