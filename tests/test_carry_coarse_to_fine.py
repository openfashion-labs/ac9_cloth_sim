# SPDX-License-Identifier: GPL-3.0-or-later
"""blender -b --factory-startup --python tests/test_carry_coarse_to_fine.py -- <package parent> [<result.json>]

A result carried onto a finer mesh of the same piece (another Particle Distance): an outline point of the fine mesh
outside the coarse mesh must not land on a point another vertex already has.

The case behind it: Simulate after a piece's Particle Distance went from 12 mm to 5 mm. The coarse outline cut a corner
of the pattern with a chord, so a fine outline point lay outside the coarse mesh, beyond a coarse corner along an edge.
The carry clamped its weights (1.18, 0, -0.18) to (1, 0, 0): it landed exactly on that corner, where the next fine
vertex already was. The triangle of the two had zero area, the solver dropped it, and the run stopped before its first
frame. The carry now carries an outside point on along the nearest triangle (dress._bary_map, extend). This test
builds that corner from synthetic numbers and checks both: the clamp squashes it (so the test can fail), the carry the
start uses does not."""
import json
import sys
import unittest

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import numpy as np
import ac9_cloth_sim  # noqa: F401
from ac9_cloth_sim import dress as D
from ac9_cloth_sim.core import start_check as CS

# The pattern: a 120 x 100 mm panel whose corner (120, 0) the coarse mesh cuts with a chord (100, 0) - (120, 100).
COARSE_V = np.array([[0, 0], [100, 0], [120, 100], [0, 100]], float)
COARSE_T = np.array([[0, 1, 2], [0, 2, 3]])
# the fine mesh keeps the coarse corner (100, 0) and reaches the pattern's own corner (120, 0)
FINE_V = np.array([[0, 0], [100, 0], [120, 0], [120, 100], [0, 100], [60, 50]], float)
FINE_T = np.array([[0, 1, 5], [1, 2, 5], [2, 3, 5], [3, 4, 5], [4, 0, 5]])


def wrapped(V, r=80.0):
    """The coarse result's shape (mm): the panel wrapped round a vertical cylinder of radius r, as round a leg."""
    a = V[:, 0] / r
    return np.c_[r * np.sin(a), r * (1.0 - np.cos(a)), V[:, 1]]


def area_ratio(X, V, T):
    """Each triangle's 3D area over its pattern area."""
    a3 = 0.5 * np.linalg.norm(np.cross(X[T[:, 1]] - X[T[:, 0]], X[T[:, 2]] - X[T[:, 0]]), axis=1)
    e1, e2 = V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]
    return a3 / (0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]))


class CarryCoarseToFine(unittest.TestCase):
    def setUp(self):
        self.Xc = wrapped(COARSE_V)

    def squashed(self, X):
        return CS.squashed_starts(X, FINE_T, np.zeros(len(FINE_T), np.int64), FINE_V, ['panel'])

    def test_clamped_carry_squashes_the_corner(self):
        # the old rule: weights clamped. The fine corner lands on the coarse corner the next vertex has
        X = D.carry_shape(FINE_V, COARSE_V, COARSE_T, self.Xc, extend=False)
        self.assertLess(np.linalg.norm(X[2] - X[1]), 1e-9)
        self.assertEqual(self.squashed(X), {'panel': 1})

    def test_start_carry_keeps_every_triangle(self):
        # the rule the start uses (dress._from_last_result -> carry_shape, extend by default)
        X = D.carry_shape(FINE_V, COARSE_V, COARSE_T, self.Xc)
        self.assertEqual(self.squashed(X), {})
        self.assertGreater(float(np.linalg.norm(X[2] - X[1])), 10.0)
        # no triangle near flat either: a tenth of its pattern area at least
        self.assertGreater(float(area_ratio(X, FINE_V, FINE_T).min()), 0.1)

    def test_from_last_result_uses_the_extending_carry(self):
        # the Live / Continue start goes through carry_shape with its default (extend), not the clamped mapper
        import inspect
        src = inspect.getsource(D._from_last_result)
        self.assertIn('carry_shape(V, Vr, T, Xr)', src)
        self.assertTrue(inspect.signature(D.carry_shape).parameters['extend'].default)


if __name__ == '__main__':
    res = unittest.main(argv=['x'], exit=False, verbosity=2).result
    out = {'ran': res.testsRun, 'failures': len(res.failures), 'errors': len(res.errors),
           'ok': res.wasSuccessful()}
    if len(args) > 1:
        json.dump(out, open(args[1], 'w', encoding='utf-8'))
    sys.exit(0 if res.wasSuccessful() else 1)
