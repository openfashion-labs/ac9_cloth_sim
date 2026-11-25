"""The Live's start-up recovery: the Live stage carries untangle_free (only while sewing, a stop rule) and deep_push
(ending with it); Accurate leaves both out instead of refusing the Live; the GPU crossing search finds the same
crossing edges as the CPU one (run on Warp's CPU device, no GPU needed)."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ac9_cloth_sim.core import recipe
from solver import fast_live_contract as F


def _spec():
    return {'pieces': [{'name': 'A'}, {'name': 'B'}], 'seams': [], 'placement': {}, 'folds': []}


def _grid(n, z, piece_shift):
    """an n x n grid of triangles in the plane z (vertical when piece_shift is None)"""
    u = np.linspace(-0.05, 0.05, n)
    P = np.array([[x, y, z] for y in u for x in u], np.float64)
    if piece_shift is not None:
        P = P[:, [0, 2, 1]] + piece_shift           # stand it up: it passes through the flat grid
    T = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            T += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    return P, np.array(T, np.int64)


class LiveUntangle(unittest.TestCase):
    def test_live_stage_carries_the_recovery(self):
        kind, stg = recipe.continue_stages(_spec(), ['A', 'B'], 'p.npz', [0.0, 1.0, 1.0], live=True)
        cfg = stg[0][1]
        self.assertEqual(kind, 'live')
        self.assertTrue(cfg['untangle_free']['sewing_only'])
        self.assertEqual(cfg['untangle_free']['max_frames'], 30)
        self.assertTrue(cfg['deep_push']['with_free'])
        # not shared between runs
        self.assertIsNot(cfg['untangle_free'], recipe.LIVE_UNTANGLE)

    def test_continue_without_live_has_no_recovery(self):
        kind, stg = recipe.continue_stages(_spec(), ['A', 'B'], 'p.npz', [0.0, 1.0, 1.0], live=False)
        for _name, cfg in stg:
            self.assertNotIn('untangle_free', cfg)
            self.assertNotIn('deep_push', cfg)

    def test_accurate_leaves_the_live_recovery_out(self):
        cfg = {'fps': 60, 'live': True, 'untangle_free': dict(recipe.LIVE_UNTANGLE),
               'deep_push': dict(recipe.LIVE_DEEP_PUSH)}
        out = F.configure(cfg)
        self.assertNotIn('untangle_free', out)
        self.assertNotIn('deep_push', out)
        self.assertIn('untangle_free', cfg)          # the input is not changed

    def test_accurate_still_refuses_an_asked_untangle(self):
        with self.assertRaises(ValueError):
            F.configure({'fps': 60, 'live': True, 'untangle_free': {'rings': 2}})

    def test_gpu_search_matches_cpu(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))   # as the solver imports them
        from crossings import crossing_edges
        from crossings_gpu import CrossingSearch
        Pa, Ta = _grid(8, 0.0, None)     # 8: no row on the other grid's plane
        Pb, Tb = _grid(8, 0.0, np.array([0.003, 0.0, 0.0]))
        X = np.vstack([Pa, Pb]); F_ = np.vstack([Ta, Tb + len(Pa)])
        vp = np.r_[np.zeros(len(Pa), int), np.ones(len(Pb), int)]
        E = np.unique(np.sort(np.concatenate([F_[:, [0, 1]], F_[:, [1, 2]], F_[:, [2, 0]]]), 1), axis=0)
        ce = np.zeros(len(E), bool)
        for a in (0, 1):
            ia, ib = np.nonzero(vp[E[:, 0]] == a)[0], np.nonzero(vp[F_[:, 0]] != a)[0]
            ei, _ti = crossing_edges(X, E[ia], F_[ib])
            ce[ia[ei]] = True
        self.assertGreater(ce.sum(), 0)
        ge, gt = CrossingSearch(F_, E, vp, 'cpu').run(X)
        self.assertTrue(np.array_equal(ce, ge))
        self.assertGreater(gt.sum(), 0)
        # pulled apart: nothing crosses
        X2 = X.copy(); X2[len(Pa):, 2] += 0.2
        ge2, gt2 = CrossingSearch(F_, E, vp, 'cpu').run(X2)
        self.assertEqual(int(ge2.sum()) + int(gt2.sum()), 0)


if __name__ == '__main__':
    unittest.main()
