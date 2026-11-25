# SPDX-License-Identifier: GPL-3.0-or-later
"""blender -b --factory-startup --python tests/test_lay_round.py -- <package parent> <result.json>

The one wrap (dress._lay_round) lays every point where the two wraps it replaced did: an open piece (_wrap) and a
band closed once round (_ring_wrap, at its own radius or a given one), with and without a tilt."""
import json
import sys
import unittest

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import numpy as np
import ac9_cloth_sim  # noqa: F401
from ac9_cloth_sim import dress as D
from ac9_cloth_sim.core import body_axes as BA


def old_wrap(g, P, anchor, theta, h, r, tilt=0.0):
    r = max(r, D._min_radius(P))
    Q = np.asarray(P, float) - np.asarray(anchor, float)
    th = np.radians(theta) + Q[:, 0] / r
    Rm = np.asarray(g['R'], float)
    rad, up = BA.tilted(Q, r, tilt)
    rad = np.broadcast_to(rad, th.shape)
    return (np.asarray(g['o'], float)[None] + rad[:, None] * (np.cos(th)[:, None] * Rm[0] + np.sin(th)[:, None] * Rm[1])
            + (h + up)[:, None] * Rm[2])


def old_ring_wrap(g, P, anchor, theta, h, width, tilt=0.0, radius=None):
    Q = np.asarray(P, float) - np.asarray(anchor, float)
    r = (width + D.RING_GAP) / (2 * np.pi)
    th = np.radians(theta) + Q[:, 0] * (2 * np.pi * (1 - D.RING_GAP / (width + D.RING_GAP))) / width
    Rm = np.asarray(g['R'], float)
    rad, up = BA.tilted(Q, r if radius is None else radius, tilt)
    rad = np.broadcast_to(rad, th.shape)
    return (np.asarray(g['o'], float)[None] + rad[:, None] * (np.cos(th)[:, None] * Rm[0] + np.sin(th)[:, None] * Rm[1])
            + (h + up)[:, None] * Rm[2]), r


def guide(rng):
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    return {'R': q, 'o': rng.normal(size=3) * 100, 'r': 60.0}


class LayRound(unittest.TestCase):
    def test_same_points(self):
        rng = np.random.default_rng(7)
        worst = 0.0
        for k in range(200):
            g = guide(rng)
            w = rng.uniform(40, 900)
            P = np.c_[rng.uniform(0, w, 300), rng.uniform(0, 120, 300)]
            anchor = np.array([w * rng.uniform(0, 1), rng.uniform(0, 120)])
            th, h = rng.uniform(-180, 180), rng.uniform(-300, 300)
            tilt = 0.0 if k % 3 == 0 else rng.uniform(-180, 180)
            r = rng.uniform(20, 250)
            worst = max(worst, float(np.abs(D._wrap(g, P, anchor, th, h, r, tilt)
                                            - old_wrap(g, P, anchor, th, h, r, tilt)).max()))
            for radius in (None, rng.uniform(20, 250)):
                a, ra = D._ring_wrap(g, P, anchor, th, h, w, tilt, radius=radius)
                b, rb = old_ring_wrap(g, P, anchor, th, h, w, tilt, radius=radius)
                self.assertEqual(ra, rb)
                worst = max(worst, float(np.abs(a - b).max()))
        self.worst = worst
        RESULT['worst_mm'] = worst
        self.assertLess(worst, 1e-9)


RESULT = {}
if __name__ == '__main__':
    r = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LayRound))
    RESULT['ok'] = r.wasSuccessful()
    with open(args[1], 'w', encoding='utf-8') as f:
        json.dump(RESULT, f)
