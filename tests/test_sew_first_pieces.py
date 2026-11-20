"""sew_first per piece (solver/sew_first.py): a collar placed by a sewn garment floats alone; pieces placed apart all
float until their seams close; a piece with no seam is sewn from the start; the widest-gap / stalled / max_frames
rules close everything."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
from sew_first import PieceSewing


def _garment(collar_gap_mm=40.0, body_gap_mm=0.5):
    # pieces 0, 1 = body (one seam), 2 = collar sewn to piece 0, 3 = a pocket with no seam
    X = np.zeros((8, 3))
    vp = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    E = np.array([[0, 2], [1, 3], [0, 4], [1, 5]])
    X[2] = X[0] + [body_gap_mm / 1000.0, 0, 0]; X[3] = X[1] + [body_gap_mm / 1000.0, 0, 0]
    X[4] = X[0] + [0, 0, collar_gap_mm / 1000.0]; X[5] = X[1] + [0, 0, collar_gap_mm / 1000.0]
    X[1] = [0.1, 0, 0]; X[3] = X[1] + [body_gap_mm / 1000.0, 0, 0]; X[5] = X[1] + [0, 0, collar_gap_mm / 1000.0]
    return X, vp, E


class PieceSewingTest(unittest.TestCase):
    def test_collar_floats_alone_by_a_sewn_body(self):
        X, vp, E = _garment()
        ps = PieceSewing(E, vp, 4)
        new = ps.step(1, X)
        self.assertEqual(ps.mode, 'per_piece')
        self.assertEqual(sorted(new.tolist()), [0, 1, 3])
        self.assertEqual(ps.floating().tolist(), [False, False, True, False])
        X[4] = X[0]; X[5] = X[1]
        self.assertEqual(ps.step(40, X).tolist(), [2])
        self.assertFalse(ps.floating().any())

    def test_pieces_placed_apart_all_float(self):
        X, vp, E = _garment(collar_gap_mm=40.0, body_gap_mm=60.0)
        ps = PieceSewing(E, vp, 4)
        ps.step(1, X)
        self.assertEqual(ps.floating().tolist(), [True, True, True, False])

    def test_pieces_placed_apart_join_together(self):
        X, vp, E = _garment(collar_gap_mm=40.0, body_gap_mm=60.0)
        ps = PieceSewing(E, vp, 4)
        ps.step(1, X)
        self.assertEqual(ps.mode, 'together')
        X[2] = X[0]; X[3] = X[1]                    # the body closes first: still floating, all join together
        ps.step(30, X)
        self.assertEqual(ps.floating().tolist(), [True, True, True, False])
        X[4] = X[0]; X[5] = X[1]
        self.assertEqual(sorted(ps.step(50, X).tolist()), [0, 1, 2])

    def test_stalled_gap_counts_as_closed(self):
        X, vp, E = _garment(collar_gap_mm=4.0, body_gap_mm=0.5)
        ps = PieceSewing(E, vp, 4, gap_mm=3.0)
        for f in range(1, 40):
            ps.step(f, X)
        # p99 of 4 stitches is near the widest, so this one does not stall; max_frames does
        self.assertTrue(ps.floating()[2])
        ps2 = PieceSewing(E, vp, 4, gap_mm=3.0, max_frames=20)
        for f in range(1, 25):
            ps2.step(f, X)
        self.assertFalse(ps2.floating().any())
        self.assertEqual(ps2.all_closed_by, 'max_frames')


if __name__ == '__main__':
    unittest.main()
