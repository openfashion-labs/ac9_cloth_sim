"""A piece's Layer reaches only the vertices away from a seam joining two layers; a narrow piece sewn along its edge
to another layer loses all of them, and that is found before the run (solver/layer_reach.py, used by the solver and
by dress.py)."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
import layer_reach as LR


def strip(nx, ny, x0=0.0):
    """A grid of nx x ny vertices as triangles: (vertices (n, 2), triangles (m, 3))."""
    v = np.array([(x0 + i, j) for j in range(ny) for i in range(nx)], float)
    t = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            t += [(a, a + 1, a + nx), (a + 1, a + nx + 1, a + nx)]
    return v, np.array(t)


def two_pieces(narrow_nx):
    """A wide piece (layer 0) and a narrow one (layer 1), the narrow one's left edge sewn to the wide one's right edge."""
    va, ta = strip(12, 10)
    vb, tb = strip(narrow_nx, 10, x0=20.0)
    n_a = len(va)
    tris = np.vstack([ta, tb + n_a])
    st = np.array([(j * 12 + 11, n_a + j * narrow_nx) for j in range(10)])
    v_piece = np.r_[np.zeros(n_a, int), np.ones(len(vb), int)]
    return tris, st, v_piece


class LayerReach(unittest.TestCase):
    def test_narrow_piece_sewn_along_its_edge_is_not_reached(self):
        tris, st, vp = two_pieces(3)
        layers = {'band': 1}
        lay = np.array([0, 1])[vp]
        reach = LR.per_piece(LR.left_out(tris, st, lay), vp, ['body', 'band'], layers)
        self.assertEqual(reach, {'band': [0, 30]})
        self.assertEqual(LR.short(reach), [('band', 0, 30)])

    def test_wide_piece_keeps_its_far_side(self):
        tris, st, vp = two_pieces(10)
        lay = np.array([0, 1])[vp]
        reach = LR.per_piece(LR.left_out(tris, st, lay), vp, ['body', 'band'], {'band': 1})
        r, t = reach['band']
        self.assertEqual(t, 100)
        self.assertEqual(r, 60)             # 4 columns of 10 left out: the sewn one and 3 rings
        self.assertEqual(LR.short(reach), [])

    def test_same_layer_seams_leave_nothing_out(self):
        tris, st, vp = two_pieces(3)
        ex = LR.left_out(tris, st, np.zeros(len(vp), int))
        self.assertFalse(ex.any())

    def test_rings_zero_leaves_only_the_stitched_vertices(self):
        tris, st, vp = two_pieces(3)
        ex = LR.left_out(tris, st, np.array([0, 1])[vp], rings=0)
        self.assertEqual(int(ex.sum()), 20)

    def test_exact_only_when_the_solver_leaves_out_by_rings(self):
        self.assertTrue(LR.exact({'layers': {'band': 1}}))
        self.assertTrue(LR.exact({'one_layer': [{'piece': 'a', 'verts': []}]}))
        self.assertFalse(LR.exact({'layers_pair_exclusions': {'radius_mm': 6.0}}))
        self.assertFalse(LR.exact({'one_layer': [{'piece': 'a', 'line': 'L1', 'verts': [1, 2], 'roll': [3, 4]}]}))
        self.assertFalse(LR.exact({'bind_verts': [{'verts': [1]}]}))
        self.assertFalse(LR.exact({'weld': {'max_mm': 1.0}}))

    def test_pieces_on_layer_zero_are_not_listed(self):
        tris, st, vp = two_pieces(3)
        reach = LR.per_piece(LR.left_out(tris, st, np.zeros(len(vp), int)), vp, ['body', 'band'], {'body': 0})
        self.assertEqual(reach, {})


if __name__ == '__main__':
    unittest.main()
