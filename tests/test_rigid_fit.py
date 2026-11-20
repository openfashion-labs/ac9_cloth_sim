# SPDX-License-Identifier: GPL-3.0-or-later
"""solver/rigid_fit.py: the turn a straight seam leaves open stays as placed; what the points decide is unchanged.
No Blender. Run: python tests/test_rigid_fit.py"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
from rigid_fit import rigid_fit, plane_normal  # noqa: E402


def rot(axis, deg):
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    r = np.radians(deg)
    return np.eye(3) + np.sin(r) * K + (1 - np.cos(r)) * K @ K


def kabsch(P, Q):
    pc, qc = P.mean(0), Q.mean(0)
    U, _, Wt = np.linalg.svd((P - pc).T @ (Q - qc))
    R = Wt.T @ np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt.T @ U.T))]) @ U.T
    return R, qc - pc @ R.T


def angle(R):
    return np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))


class StraightSeam(unittest.TestCase):
    def setUp(self):
        # a 40 x 1200 mm strip in the x-z plane, sewn along its edge x = 0
        zs = np.linspace(0, 1200, 57)
        self.edge = np.c_[np.zeros(57), np.zeros(57), zs]
        self.strip = np.vstack([self.edge, self.edge + [40, 0, 0]])
        self.vi = np.arange(57)

    def far_dir(self, X):
        d = X[57:].mean(0) - X[:57].mean(0)
        return d / np.linalg.norm(d)

    def test_shift_only_keeps_the_placed_turn(self):
        Q = self.edge + [30, -100, 5]
        R, t = rigid_fit(self.strip[self.vi], Q, 1.0, normal=plane_normal(self.strip))
        self.assertLess(angle(R), 1e-6)
        np.testing.assert_allclose(self.edge @ R.T + t, Q, atol=1e-9)

    def test_line_turned_30_deg_keeps_the_turn_about_it(self):
        Rt = rot([1, 0, 0], 30)                    # the partner's line leans 30 degrees, the strip should follow it
        Q = self.edge @ Rt.T + [5, 5, 5]
        R, t = rigid_fit(self.strip[self.vi], Q, 1.0, normal=plane_normal(self.strip))
        self.assertAlmostEqual(angle(R), 30.0, delta=0.01)
        np.testing.assert_allclose(R, Rt, atol=1e-4)

    def test_noise_on_the_partner_does_not_flip_it(self):
        rng = np.random.default_rng(3)
        ref = self.far_dir(self.strip)
        for _ in range(20):
            Q = self.edge + [30, -100, 5] + rng.normal(0, 0.5, self.edge.shape)
            R, t = rigid_fit(self.strip[self.vi], Q, 1.0, normal=plane_normal(self.strip))
            got = self.far_dir(self.strip @ R.T + t)
            self.assertLess(np.degrees(np.arccos(np.clip(got @ ref, -1, 1))), 1.0)

    def test_line_reversed_keeps_the_face(self):
        # the partner runs the other way (the line must turn 180 degrees): the face is kept, the strip turns in its plane
        Q = self.edge[::-1] + [0, 0, 0]
        n0 = plane_normal(self.strip)
        R, t = rigid_fit(self.strip[self.vi], Q, 1.0, normal=n0)
        self.assertGreater(abs(n0 @ (R @ n0)), 0.99)
        self.assertGreater(n0 @ (R @ n0), 0.99)
        np.testing.assert_allclose(self.edge @ R.T + t, Q, atol=1e-6)

    def test_meters_and_millimeters_agree(self):
        Q = self.edge @ rot([1, 2, 0], 25).T + [30, -100, 5]
        Rmm, _ = rigid_fit(self.strip[self.vi], Q, 1.0, normal=plane_normal(self.strip))
        Rm, _ = rigid_fit(self.strip[self.vi] / 1000, Q / 1000, 1e-3, normal=plane_normal(self.strip))
        np.testing.assert_allclose(Rmm, Rm, atol=1e-9)

    def test_all_points_at_one_place(self):
        P = np.zeros((5, 3))
        R, t = rigid_fit(P, P + [1, 2, 3], 1.0)
        np.testing.assert_allclose(R, np.eye(3), atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(R), 1.0)
        np.testing.assert_allclose(t, [1, 2, 3])


class DecidedByThePoints(unittest.TestCase):
    """Spread wide (a curved seam): the same as plain Kabsch within a tiny turn."""

    def test_wide_spread_matches_kabsch(self):
        rng = np.random.default_rng(7)
        for spread in (30.0, 100.0, 400.0):
            P = rng.normal(0, 1, (40, 3)) * [400, spread, spread * 0.5]
            Rt = rot(rng.normal(size=3), rng.uniform(0, 170))
            Q = P @ Rt.T + rng.normal(0, 0.3, P.shape) + [10, -20, 30]
            R0, t0 = kabsch(P, Q)
            R1, t1 = rigid_fit(P, Q, 1.0, normal=plane_normal(P))
            with self.subTest(spread=spread):
                self.assertLess(angle(R0.T @ R1), 0.1)
                g0 = np.linalg.norm(P @ R0.T + t0 - Q, axis=1).mean()
                g1 = np.linalg.norm(P @ R1.T + t1 - Q, axis=1).mean()
                self.assertLess(g1 - g0, 0.05)

    def test_bowed_seam_changes_smoothly(self):
        # a 600 mm seam bowed by 0 .. 60 mm, its partner turned 40 degrees about the seam: the more the bow decides
        # the turn, the nearer the fit comes to it, with no jump (measured: 40.0, 38.8 at 1 mm, 21.6 at 5, 8.7 at 10,
        # 1.2 at 30, 0.3 at 60 degrees off)
        zs = np.linspace(0, 600, 41)
        Rt = rot([0, 0, 1], 40)
        last = 40.01
        for bow in (0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0):
            P = np.c_[bow * np.sin(np.pi * zs / 600), np.zeros_like(zs), zs]
            Q = P @ Rt.T + [3, 4, 5]
            R, _ = rigid_fit(P, Q, 1.0, normal=[0, 1, 0])
            off = angle(Rt.T @ R)
            with self.subTest(bow=bow):
                self.assertLessEqual(off, last)
                if bow >= 30.0:
                    self.assertLess(off, 1.5)
            last = off


if __name__ == '__main__':
    unittest.main()
