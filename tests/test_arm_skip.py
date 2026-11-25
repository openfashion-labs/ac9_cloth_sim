# SPDX-License-Identifier: GPL-3.0-or-later
"""blender -b --factory-startup --python tests/test_arm_skip.py -- <package parent> <result.json>

Nothing is pushed out of the body: a piece starts where it was placed, what is in the body said. A piece round the torso keeps clear of the body without the arms, whether or not a piece is round an arm: the solver
leaves the arms out while the body is sewn. It used to count the arms when no piece was round one, and a panel placed
between the torso and a hanging arm was pushed out past the arm (and moved back when a sleeve was placed later)."""
import io
import json
import sys
import unittest
from pathlib import Path

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import numpy as np
from mathutils.bvhtree import BVHTree
import ac9_cloth_sim
from ac9_cloth_sim import dress as D


def grid(w, h, step=10):
    V = np.array([[x, y] for y in range(0, h + 1, step) for x in range(0, w + 1, step)], float)
    n = w // step + 1
    T = np.array([t for y in range(h // step) for x in range(w // step)
                  for t in ((n*y+x, n*y+x+1, n*(y+1)+x), (n*y+x+1, n*(y+1)+x+1, n*(y+1)+x))])
    return V, T


def box(lo, hi):
    """Closed box (mm) as outward triangles."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    P = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    Q = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)]
    return np.array(P, float), np.array([t for a, b, c, d in Q for t in ((a, b, c), (a, c, d))])


class ArmSkip(unittest.TestCase):
    def setUp(self):
        V, _ = grid(100, 100)
        # the pattern holds the placed pieces only (prepare's placed_only)
        self.pat = dict(names=np.array(['panel']), V0=V, nb=np.array([len(V)]), stitch=np.zeros((0, 4), int), h=10.)
        self.pat2 = dict(names=np.array(['panel', 'sleeve']), V0=V, V1=V.copy(), nb=np.array([len(V), len(V)]),
                         stitch=np.zeros((0, 4), int), h=10.)
        rect = [dict(id=str(i), pts=[a, b]) for i, (a, b) in enumerate(
            (([0, 0], [100, 0]), ([100, 0], [100, 100]), ([100, 100], [0, 100]), ([0, 100], [0, 0])))]
        eye = np.eye(3).tolist()
        self.spec = dict(name='ArmSkipFixture', units='mm', seams=[],
                         pieces=[dict(name='panel', edges=rect), dict(name='sleeve', edges=rect)],
                         guides={'torso': dict(o=[0., 0., 0.], R=eye, r=100., h0=-200., h1=200.),
                                 'arm_L': dict(o=[0., 600., 0.], R=eye, r=50., h0=-200., h1=200.)},
                         placement={'panel': dict(guide='torso', theta=0., h=0., anchor=[50., 50.])})
        # an "arm" right where the panel lies at the torso guide's radius, every face of it an arm face
        P, T = box((85., -70., -70.), (140., 70., 70.))
        self.bvh = BVHTree.FromPolygons((P * 0.001).tolist(), T.tolist(), all_triangles=True)
        self.arm = np.ones(len(T), bool)

    def radius(self, X):
        return float(np.median(np.hypot(X[:, 0], X[:, 1])))

    def test_torso_piece_ignores_the_arms_without_a_sleeve(self):
        X = D.initial_shape(self.spec, self.pat, self.bvh, None, arm_faces=self.arm)[0]
        self.assertAlmostEqual(self.radius(X[0]), 100.0, delta=0.5)

    def test_placing_a_sleeve_does_not_move_the_torso_piece(self):
        X0 = D.initial_shape(self.spec, self.pat, self.bvh, None, arm_faces=self.arm)[0][0]
        self.spec['placement']['sleeve'] = dict(guide='arm_L', theta=0., h=0., anchor=[50., 50.])
        X1 = D.initial_shape(self.spec, self.pat2, self.bvh, None, arm_faces=self.arm)[0][0]
        self.assertLess(float(np.abs(X0 - X1).max()), 1e-6)

    def test_nothing_is_pushed_out_of_the_body(self):
        # the same box as body, not arm: the piece stays on the guide, its vertices in the body said (user 2026-11-22)
        X, _r, rep, *_ = D.initial_shape(self.spec, self.pat, self.bvh, None, arm_faces=np.zeros_like(self.arm))
        self.assertAlmostEqual(self.radius(X[0]), 100.0, delta=0.5)
        self.assertGreater(len(D.IN_BODY_LAST.get('panel', [])), 0)
        self.assertTrue(any(l.startswith(D.IN_BODY + 'panel') for l in rep))

    def test_arm_skip_rule(self):
        self.assertIs(D.arm_skip({'guide': 'torso'}, self.arm), self.arm)
        self.assertIsNone(D.arm_skip({'guide': 'arm_R'}, self.arm))
        self.assertIsNone(D.arm_skip({'guide': 'torso'}, None))


if __name__ == '__main__':
    ac9_cloth_sim.register()
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArmSkip))
    Path(args[1]).write_text(json.dumps(dict(passed=result.wasSuccessful(), tests=result.testsRun,
                                             log=log.getvalue()), indent=2), encoding='utf8')
    if not result.wasSuccessful(): raise RuntimeError(log.getvalue())
