# SPDX-License-Identifier: GPL-3.0-or-later
"""Resident Live (solver/resident_runner.py) without a GPU: which changes keep the model (the structure key), which
statements of newton_vbd.py are the heavy ones a begin skips, and the shapes that are never kept."""
import os, sys, tempfile, types, unittest
from pathlib import Path
import numpy as np

SOLVER = Path(__file__).resolve().parent.parent / 'solver'
sys.path.insert(0, str(SOLVER))
import resident_runner as RR


def _npz(path, **arrays):
    np.savez(path, **arrays); return path


class StructureKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = Path(self.tmp.name)
        F = np.array([[0, 1, 2]], np.int32)
        self.idx = str(_npz(d / 'a.idx.npz', F=F, Xinit=np.zeros((3, 3)), Xclo=np.zeros((3, 3))))
        self.idx_moved = str(_npz(d / 'b.idx.npz', F=F, Xinit=np.ones((3, 3)), Xclo=np.ones((3, 3))))
        self.idx_other = str(_npz(d / 'c.idx.npz', F=F[:, ::-1].copy(), Xinit=np.zeros((3, 3)), Xclo=np.zeros((3, 3))))
        self.avatar = str(_npz(d / 'av.npz', V=np.zeros((3, 3))))
        self.cfg = dict(edge_ke=1e-4, edge_kd=1e-8, density=0.14, tri_ke=280.0, tri_ka=280.0, tri_kd=1e-3, tri_ks=26.0,
                        seam_bend={'pairs': 'all', 'ke': 3e-4, 'rest_deg': 0}, grab_file=str(d / 'g1.json'),
                        fabric_pieces={'P': {'D': 5e-5, 'rho': 0.17}}, mi={'D': 6.5e-7, 'ref_mm': 6.0})

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, idx=None, **change):
        c = dict(self.cfg); c.update(change)
        return RR.structure_key(idx or self.idx, self.avatar, c)

    def test_values_and_start_shape_keep_the_model(self):
        base = self.key()
        self.assertEqual(base, self.key(idx=self.idx_moved))                       # another start shape
        self.assertEqual(base, self.key(edge_ke=2e-4, tri_ke=300.0, density=0.2))  # fabric numbers
        self.assertEqual(base, self.key(seam_bend={'pairs': 'all', 'ke': 5e-4, 'rest_deg': 0}))
        self.assertEqual(base, self.key(fabric_pieces={'P': {'D': 6e-5, 'rho': 0.2}}, mi={'D': 7e-7, 'ref_mm': 6.0}))
        self.assertEqual(base, self.key(grab_file='elsewhere.json'))              # where a job's files are

    def test_structure_builds_fresh(self):
        base = self.key()
        self.assertNotEqual(base, self.key(idx=self.idx_other))                    # another mesh
        self.assertNotEqual(base, self.key(tri_ks=30.0))                           # read outside finalize_values
        self.assertNotEqual(base, self.key(fabric_pieces={'P': {'D': 5e-5, 'rho': 0.17, 'ku': 300.0}}))
        self.assertNotEqual(base, self.key(fabric_pieces={'Q': {'D': 5e-5, 'rho': 0.17}}))
        self.assertNotEqual(base, self.key(mi={'D': 0.0, 'ref_mm': 6.0}))            # the block on / off
        self.assertNotEqual(base, self.key(seam_bend={'pairs': 'all', 'ke': 3e-4, 'rest_deg': 10}))


class Statements(unittest.TestCase):
    def test_newton_vbd_split(self):
        fake = types.ModuleType('warp')
        old = sys.modules.get('warp'); sys.modules['warp'] = fake
        try:
            R = RR.Resident(SOLVER / 'newton_vbd.py', SOLVER)
        finally:
            if old is None: sys.modules.pop('warp', None)
            else: sys.modules['warp'] = old
        self.assertLess(R.i_builder, R.i_final); self.assertLess(R.i_final, R.loop_i)
        heads = sorted(R.heads[i].split('(')[0].split(chr(10))[0] for i in R.i_heavy)
        # the solver, the collision pipeline and the Native 3 block only (not the checkpoint branch)
        self.assertEqual(heads, ['if NATIVE_THREE:', 'pipe = newton.CollisionPipeline', 'solver = newton.solvers.SolverVBD'])


class NotKept(unittest.TestCase):
    def test_reasons(self):
        plain = dict(cfg={}, BOUND_COUPLING=False, WELD=None)
        self.assertEqual([w for _, t, w in RR.NOT_KEPT if t(plain)], [])
        for g in (dict(plain, BOUND_COUPLING=True), dict(plain, WELD=object()), dict(plain, cfg={'anchor_ke': 1.0}),
                  dict(plain, cfg={'seam_paths': [[1, 2]]}), dict(plain, cfg={'sym_color': True, 'mirror_map': 'm.npz'}),
                  dict(plain, cfg={'ghost_pieces': ['P']})):
            self.assertEqual(len([w for _, t, w in RR.NOT_KEPT if t(g)]), 1, g)


if __name__ == '__main__':
    unittest.main()
