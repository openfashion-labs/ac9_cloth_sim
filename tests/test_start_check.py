"""Start check (core/start_check.py): each piece's start against the shape on screen at the press."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from ac9_cloth_sim.core import start_check as CS


def _square(off=(0.0, 0.0, 0.0)):
    V = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    T = np.array([[0, 1, 2], [0, 2, 3]])
    X = np.c_[V, np.zeros(4)] + np.asarray(off, float)
    return V, T, X


def _mapper(Q, V, T, X):            # the layout points are the shape's own here
    return np.array([X[np.argmin(np.linalg.norm(V - q, axis=1))] for q in Q])


class StartCheck(unittest.TestCase):
    def setUp(self):
        V, _T, X = _square()
        self.flat = np.vstack([V, V])
        self.X = np.vstack([X, X + [0, 0, 100.0]])

    def test_same_shape_passes(self):
        V, T, X = _square()
        screen = {'A': ('placed', (V, T, X), ''), 'B': ('result', (V, T, X + [0, 0, 100.0]), '')}
        res = CS.compare(screen, ['A', 'B'], self.flat, [0, 4], self.X, _mapper)
        self.assertTrue(all(r['checked'] and not r['over'] for r in res.values()))
        self.assertEqual(CS.lines(res)[1], [])

    def test_a_start_off_the_screen_is_found(self):
        V, T, X = _square()
        screen = {'A': ('placed', (V, T, X + [0, 73.0, 0]), ''), 'B': ('rule', None, '')}
        res = CS.compare(screen, ['A', 'B'], self.flat, [0, 4], self.X, _mapper)
        self.assertEqual(res['A']['over'], [0, 1, 2, 3])
        self.assertAlmostEqual(res['A']['dmax_mm'], 73.0)
        self.assertFalse(res['B']['checked'])
        head, off = CS.lines(res)
        self.assertIn("1 do NOT", head)
        self.assertEqual(len(off), 1)

    def test_other_layout_goes_through_the_mapper(self):
        V, T, X = _square()
        screen = {'A': ('placed', (V[::-1].copy(), T, X[::-1].copy()), '')}
        res = CS.compare(screen, ['A'], self.flat[:4], [0], self.X[:4], _mapper)
        self.assertTrue(res['A']['checked'])
        self.assertEqual(res['A']['over'], [])

    def test_mirrored_half_of_another_size_is_not_compared(self):
        V, T, X = _square()
        screen = {'A': ('mirrored', (V[:3], T[:1], X[:3]), '')}
        res = CS.compare(screen, ['A'], self.flat[:4], [0], self.X[:4], _mapper)
        self.assertFalse(res['A']['checked'])

    def test_a_hand_move_lost_to_the_rule_is_said(self):
        screen = {'A': ('rule', None, '', 'hand move'), 'B': ('rule', None, '', '')}
        res = CS.compare(screen, ['A', 'B'], self.flat, [0, 4], self.X, lambda *a: None)
        self.assertEqual(res['A']['over'], [0, 1, 2, 3])
        self.assertFalse(res['B']['checked'])
        self.assertIn("without its hand move", CS.lines(res)[1][0])

    def test_a_piece_not_read_is_said(self):
        res = CS.compare({}, ['A'], self.flat[:4], [0], self.X[:4], _mapper)
        self.assertEqual(res['A']['source'], 'unknown')


if __name__ == '__main__':
    unittest.main()
