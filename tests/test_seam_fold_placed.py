# SPDX-License-Identifier: GPL-3.0-or-later
"""A seam's fold on the adjusted shape (place_fold._seam_hinge): the angle is the one between the two pieces, read
against the partner's own edge, so a piece placed tilted out turns to the set angle (it moved 0 mm at any angle
when the cylinder was developed), a tilt is not counted twice, and the piece keeps its size.

Run: blender -b --factory-startup --python tests/test_seam_fold_placed.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import numpy as np                                           # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import place_fold as PF                  # noqa: E402

RESULTS = {}


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    ac9_cloth_sim.unregister()
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps(RESULTS, indent=1), encoding='utf-8')


def collar(partner_down=True):
    """A flat 100 x 40 mm piece placed lying out from its seam (the row y = 0, along x) away from the axis, and the
    partner running down from the seam (a stand standing under it): 90 deg out from straight on."""
    xs, ys = np.meshgrid(np.arange(0.0, 101.0, 10.0), np.arange(0.0, 41.0, 10.0))
    V = np.c_[xs.ravel(), ys.ravel()]
    X = np.c_[V, np.zeros(len(V))]
    roll = np.nonzero(V[:, 1] == 0)[0]
    down = np.array([0.0, 0.0, -1.0 if partner_down else 1.0])
    return {'V': V, 'Xid': X, 'roll': roll, 'axis': np.array([50.0, -200.0]),
            'partner_dir': {int(r): down for r in roll}}


def pairwise(X):
    return np.linalg.norm(X[:, None] - X[None], axis=2)


class SeamFoldPlaced(unittest.TestCase):
    def test_tilted_piece_turns_to_the_angle(self):
        info = collar()
        far = np.nonzero(info['V'][:, 1] == 40.0)[0]
        out = {}
        for deg, inward, want in ((90.0, False, (40.0, 0.0)), (30.0, False, (40 * np.sin(np.radians(30)), 40 * np.cos(np.radians(30)))),
                                  (150.0, False, (40 * np.sin(np.radians(150)), 40 * np.cos(np.radians(150)))),
                                  (90.0, True, (-40.0, 0.0))):
            X, n = PF._seam_hinge(dict(info), deg, inward)
            self.assertEqual(n, len(info['V']) - len(info['roll']))
            yz = X[far][:, 1:].mean(0)
            out[f"{'in' if inward else 'out'} {deg:g}"] = {'far_edge_yz': np.round(yz, 3).tolist()}
            self.assertLess(np.abs(yz - np.array(want)).max(), 1e-6, (deg, inward, yz))
            self.assertLess(np.abs(pairwise(X) - pairwise(info['Xid'])).max(), 1e-6)       # turned, not stretched
            self.assertLess(np.abs(X[info['roll']] - info['Xid'][info['roll']]).max(), 1e-9)  # the seam stays
        RESULTS['tilted'] = out

    def test_angle_now_is_not_added_twice(self):
        info = collar()
        i2 = dict(info)
        X, _n = PF._seam_hinge(i2, 90.0, False)
        self.assertAlmostEqual(i2['turned_from_deg'], 90.0, places=6)
        self.assertLess(np.abs(X - info['Xid']).max(), 1e-6)          # at the angle already: nothing moves

    def test_no_partner_shape_falls_back(self):
        info = dict(collar(), partner_dir={})
        self.assertEqual(PF._seam_hinge(info, 90.0, False), (None, 0))


if __name__ == '__main__':
    r = unittest.main(argv=[sys.argv[0]], exit=False).result
    if not r.wasSuccessful():
        sys.exit(1)
