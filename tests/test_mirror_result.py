"""Mirror 3D to Pair's core (core/mirror_result.py): which side copies onto which, the copy of a mirrored mesh and of
another mesh, the lapped fronts, the seams it opens."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import mirror_result as MR


def _spec(seam_line=False):
    a = {'piece': 'L', 'line': 'C1'} if seam_line else {'piece': 'L', 'from': ['e1', 0.0], 'to': ['e1', 1.0]}
    return {'pieces': [{'name': 'L', 'bom': {'family': 'f'}}, {'name': 'R', 'bom': {'family': 'f'}, 'mirrored': True},
                       {'name': 'B'}],
            'seams': [{'id': 's1', 'a': a, 'b': {'piece': 'R', 'from': ['e1', 0.0], 'to': ['e1', 1.0]}}]}


def _grid(x0, n=4, h=10.0):
    g = np.array([[x0 + i * h, j * h] for j in range(n) for i in range(n)], float)
    T = []
    for j in range(n - 1):
        for i in range(n - 1):
            v = j * n + i
            T += [[v, v + 1, v + n], [v + 1, v + n + 1, v + n]]
    return g, np.array(T)


class MirrorResult(unittest.TestCase):
    def test_plan(self):
        sp = _spec()
        self.assertEqual(MR.plan(sp, ['L'], 'L')[0], [('L', 'R')])
        self.assertEqual(MR.plan(sp, ['R'], 'R')[0], [('R', 'L')])
        self.assertEqual(MR.plan(sp, ['L', 'R'], 'R')[0], [('R', 'L')])
        todo, notes = MR.plan(sp, ['B'], 'B')
        self.assertEqual(todo, [])
        self.assertTrue(notes)

    def test_closure_only_when_marked(self):
        sp = _spec(True)                                   # a seam along a line, but nobody marked a closure
        self.assertEqual(MR.closure_top(sp, 'L', 'R', lambda n: 'left' if n == 'L' else 'right'), (False, None, None))
        sp['closure_over'] = 'right'                       # Front Over alone does not mark one
        self.assertFalse(MR.closure_top(sp, 'L', 'R', lambda n: 'left' if n == 'L' else 'right')[0])
        sp['buttons'] = [{'piece': 'L', 'hole': {'piece': 'R', 'pt': [0, 0]}}]
        self.assertEqual(MR.closure_top(sp, 'L', 'R', lambda n: 'left' if n == 'L' else 'right'), (True, 'R', 'front'))
        del sp['closure_over']                             # then the buttonholes' side
        self.assertEqual(MR.closure_top(sp, 'L', 'R', lambda n: 'left' if n == 'L' else 'right'), (True, 'R', 'buttons'))
        sp['seams'][0]['over'] = 'a'                       # the seam's Over first
        self.assertEqual(MR.closure_top(sp, 'L', 'R', lambda n: 'left' if n == 'L' else 'right'), (True, 'L', 'seam'))

    def _scene(self, twin_flat):
        fs, T = _grid(0.0)
        n = len(fs)
        flat = np.vstack([fs, twin_flat])
        F = np.vstack([T, T + n]) if len(twin_flat) == n else np.vstack([T, _grid(100.0, 5, 7.5)[1] + n])
        X = np.zeros((len(flat), 3))
        X[:n] = np.c_[fs[:, 0] + 50.0, np.sin(fs[:, 1] / 9.0) * 5.0, fs[:, 1]]       # the source, right of x = 0
        return X, flat, F, (0, n), (n, len(flat))

    def test_same_mesh_copied_vertex_for_vertex(self):
        fs, _ = _grid(0.0)
        tw = np.c_[200.0 - fs[:, 0], fs[:, 1]]            # the twin: the layout mirrored
        X, flat, F, rs, rt = self._scene(tw)
        Y, how = MR.mirror_piece(X, flat, F, rs, rt, 0.0)
        self.assertEqual(how, 'same')
        np.testing.assert_allclose(Y, X[:16] * [-1, 1, 1])

    def test_other_mesh_lies_on_the_mirrored_surface(self):
        tw, _ = _grid(100.0, 5, 7.5)                       # another mesh of the same 30 x 30 outline
        X, flat, F, rs, rt = self._scene(tw)
        Y, how = MR.mirror_piece(X, flat, F, rs, rt, 0.0)
        self.assertEqual(how, 'mapped')
        self.assertLess(Y[:, 0].max(), 0.0)                # on the other side of the center
        # the mirrored source is x = -(u + 50), y = sin(v / 9) * 5, z = v: the copy within linear interpolation of it
        u = -Y[:, 0] - 50.0
        self.assertTrue(np.all((u > -1e-6) & (u < 30 + 1e-6)))
        self.assertLess(np.abs(Y[:, 1] - np.sin(Y[:, 2] / 9.0) * 5.0).max(), 1.0)      # chord error of a 10 mm grid on this curve: 0.77

    def test_lap_moves_only_the_overlap(self):
        Xs = np.array([[100.0, 0.0, 0.0], [100.0, 0.0, 10.0]])
        Xt = np.array([[101.0, 0.0, 0.0], [100.0, 0.0, 30.0], [0.0, 100.0, 0.0]])
        Y, n = MR.lap(Xt, Xs, (0.0, 0.0), top=True)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(Y[0, 0], 101.0 + MR.GAP)
        np.testing.assert_allclose(Y[1:], Xt[1:])
        Y, _ = MR.lap(Xt, Xs, (0.0, 0.0), top=False)
        self.assertAlmostEqual(Y[0, 0], 101.0 - MR.GAP)

    def test_opened_seams(self):
        X = np.array([[0, 0, 0], [0, 0, 0], [5, 0, 0], [0, 0, 0.0]])
        pidx = {'E_st': np.array([[0, 1], [2, 3]]), 'E_seam': np.array(['a', 'b'])}
        rng = {'L': (0, 1), 'R': (1, 3), 'B': (3, 4)}
        self.assertEqual(MR.opened_seams(X, pidx, rng, ['R']), [('B', 'R', 5.0)])


if __name__ == '__main__':
    unittest.main()
