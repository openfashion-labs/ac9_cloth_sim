"""A front / back pair (mirror_plane 'y') follows a corner or internal-line move mirrored in y: lifting the top edge
of one lifts the other's matching edge, so both get taller. Before the fix the twin got the left / right move
(-dx, +dy): the other piece got shorter by as much (a 10 mm lift: 283.6 / 283.6 mm high -> 293.6 / 273.6)."""
import sys
import unittest

import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import draft as D


def pair(plane):
    q = [[0, 0], [300, 0], [300, 280], [0, 280]]
    spec = {'version': 1, 'name': 't', 'seams': [],
            'pieces': [{'name': 'a', 'edges': [{'id': f'a_e{i}', 'pts': [q[i], q[(i + 1) % 4]]} for i in range(4)]}]}
    tw = D.mirror_copy(spec, 'a', plane=plane)
    return spec, tw


def extent(spec, name):
    C = D.corners(D.piece(spec, name))
    return C.max(0) - C.min(0)


class TwinFrontBackMove(unittest.TestCase):
    def test_lift_top_edge(self):
        for plane in ('x', 'y'):
            spec, tw = pair(plane)
            C = D.corners(D.piece(spec, 'a'))
            top = [int(k) for k in np.argsort(C[:, 1])[-2:]]
            D.move_corners(spec, 'a', {k: (0.0, 10.0) for k in top})
            np.testing.assert_allclose(extent(spec, 'a'), [300, 290], atol=1e-6)
            np.testing.assert_allclose(extent(spec, tw), [300, 290], atol=1e-6, err_msg=plane)

    def test_shift_right(self):
        for plane, want in (('x', -10.0), ('y', 10.0)):
            spec, tw = pair(plane)
            B0 = D.corners(D.piece(spec, tw)).copy()
            C = D.corners(D.piece(spec, 'a'))
            top = [int(k) for k in np.argsort(C[:, 1])[-2:]]
            _, cmap, _ = D.twin_corner_map(spec, 'a')
            D.move_corners(spec, 'a', {k: (10.0, 0.0) for k in top})
            B1 = D.corners(D.piece(spec, tw))
            for k in top:
                self.assertAlmostEqual(float(B1[cmap[k]][0] - B0[cmap[k]][0]), want, msg=plane)

    def test_internal_line(self):
        for plane, want in (('x', (-10.0, 5.0)), ('y', (10.0, -5.0))):
            spec, tw = pair(plane)
            P = [[100, 100], [200, 150]]
            lid = D.add_internal(spec, 'a', P)
            D.add_internal(spec, tw, D.mirror_fn(D.piece(spec, 'a'), D.piece(spec, tw))(P))
            ttw, tlid, _ = D._twin_internal(spec, 'a', lid)
            self.assertEqual(ttw, tw)
            Q0 = np.asarray(D.internal(D.piece(spec, tw), tlid)['pts'], float).copy()
            D.move_internal(spec, 'a', lid, 'all', (10.0, 5.0))
            Q1 = np.asarray(D.internal(D.piece(spec, tw), tlid)['pts'], float)
            np.testing.assert_allclose(Q1 - Q0, [want] * len(Q0), atol=1e-6, err_msg=plane)


if __name__ == '__main__':
    unittest.main()
