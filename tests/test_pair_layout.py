# SPDX-License-Identifier: GPL-3.0-or-later
"""A Pair (two pieces drawn separately) placed front / back lands the right way up however its second piece is drawn
(no Blender): python tests/test_pair_layout.py from the add-on folder.

Before: a Pair always mirrored left / right, and a back panel drawn upside down (its slit at the top of the pattern,
the front's at the bottom) came out with its slit at the bottom in 3D (measured on a test bag: the front's slit at
z 0.205 m, the back's at 0.0005 m). The pattern axis the second piece is drawn mirrored across is now found from the
outline and internal lines, kept apart from the world plane, and the placement mirrors through it."""
import sys
import types
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if 'ac9_cloth_sim' not in sys.modules:                 # the package without its Blender __init__
    pkg = types.ModuleType('ac9_cloth_sim')
    pkg.__path__ = [str(ROOT)]
    sys.modules['ac9_cloth_sim'] = pkg
from ac9_cloth_sim.core import spec as S                 # noqa: E402
from ac9_cloth_sim.core import draft as D                # noqa: E402

W, H = 300.0, 175.0


def rect(name, slit_y=None, x0=0.0):
    q = [[x0, 0], [x0 + W, 0], [x0 + W, H], [x0, H]]
    pc = {'name': name, 'edges': [{'id': f'{name}_e{i}', 'pts': [q[i], q[(i + 1) % 4]]} for i in range(4)]}
    if slit_y is not None:
        pc['internals'] = [{'id': f'{name}_i1', 'pts': [[x0 + 150.0, slit_y[0]], [x0 + 150.0, slit_y[1]]]}]
    return pc


def spec_of(b_slit):
    return {'version': 1, 'name': 't', 'seams': [], 'pairs': [['a', 'b']],
            'pieces': [rect('a', (10.0, 30.0)), rect('b', b_slit, x0=400.0)]}


def world(pl, ol, P):
    """Layout points P of a flat-placed piece in the world (mm), as the placement lays them."""
    f = pl['flat']
    o, R = np.asarray(f['o'], float), np.asarray(f['R'], float)
    Q = S.spun(pl, P) - np.asarray(pl['anchor'], float)
    return o + Q[:, :1] * R[0] + Q[:, 1:2] * R[1]


FRONT = {'guide': S.FLAT, 'anchor': [150.0, 87.5],
         'flat': {'o': [0.0, -1400.0, 87.5], 'R': [[1, 0, 0], [0, 0, 1], [0, -1, 0]]}}   # standing, facing -y


class PairLayout(unittest.TestCase):
    def test_best_layout(self):
        for b_slit, plane, want in (((145.0, 165.0), 'y', 'y'),       # drawn upside down: the y mirror
                                    ((10.0, 30.0), 'y', 'x'),         # copied as is: the x mirror (rectangle)
                                    ((145.0, 165.0), 'x', 'y'),
                                    ((10.0, 30.0), 'x', 'x'),
                                    (None, 'y', 'y')):                 # no slit on either: a tie, the plane's axis
            sp = spec_of(b_slit)
            if b_slit is None:
                sp['pieces'][0].pop('internals')
            got, gap = S.best_layout(D.piece(sp, 'a'), D.piece(sp, 'b'), plane)
            self.assertEqual(got, want, (b_slit, plane, gap))

    def test_front_back_right_way_up(self):
        for b_slit in ((145.0, 165.0), (10.0, 30.0)):
            sp = spec_of(b_slit)
            layout, _, changed = D.set_pair_layout(sp, 'a', 'b', 'y')
            self.assertTrue(changed)
            oa, ob = S.Outline(D.piece(sp, 'a')), S.Outline(D.piece(sp, 'b'))
            self.assertEqual(oa.plane, 'y')
            pb = S.mirrored_placement(FRONT, oa, ob, pose=None, cx=-1300.0)
            sa = world(FRONT, oa, np.array(D.piece(sp, 'a')['internals'][0]['pts'], float))
            sb = world(pb, ob, np.array(D.piece(sp, 'b')['internals'][0]['pts'], float))
            # the slit at the same height, the back on the other side of the plane, the same x
            np.testing.assert_allclose(sorted(sb[:, 2]), sorted(sa[:, 2]), atol=1e-6, err_msg=str(b_slit))
            np.testing.assert_allclose(sb[:, 1], 2 * -1300.0 - sa[:, 1], atol=1e-6)
            np.testing.assert_allclose(sorted(sb[:, 0]), sorted(sa[:, 0]), atol=1e-6)
            # the back's face out (+y), as the front's is (-y)
            n = np.asarray(pb['flat']['R'][2], float)
            self.assertGreater(n[1], 0.9)
            # the whole outline: every corner of b on the mirror of a corner of a
            Ca = world(FRONT, oa, oa.B)
            Cb = world(pb, ob, ob.B)
            Ma = Ca.copy()
            Ma[:, 1] = 2 * -1300.0 - Ma[:, 1]
            d = np.linalg.norm(Cb[:, None] - Ma[None], axis=-1).min(1)
            self.assertLess(d.max(), 1e-6)

    def test_old_pair_unchanged(self):
        """A Pair from before (no keys): left / right across x, as it was."""
        sp = spec_of((145.0, 165.0))
        oa, ob = S.Outline(D.piece(sp, 'a')), S.Outline(D.piece(sp, 'b'))
        self.assertEqual((oa.plane, oa.layout), ('x', 'x'))

    def test_left_right_with_upside_down_drawing(self):
        sp = spec_of((145.0, 165.0))
        layout, _, _ = D.set_pair_layout(sp, 'a', 'b', 'x')
        self.assertEqual(layout, 'y')
        oa, ob = S.Outline(D.piece(sp, 'a')), S.Outline(D.piece(sp, 'b'))
        pb = S.mirrored_placement(FRONT, oa, ob, pose=None, cx=0.0)
        sa = world(FRONT, oa, np.array(D.piece(sp, 'a')['internals'][0]['pts'], float))
        sb = world(pb, ob, np.array(D.piece(sp, 'b')['internals'][0]['pts'], float))
        np.testing.assert_allclose(sorted(sb[:, 2]), sorted(sa[:, 2]), atol=1e-6)
        np.testing.assert_allclose(sorted(sb[:, 0]), sorted(-sa[:, 0]), atol=1e-6)

    def test_back_to_left_right_drops_keys(self):
        sp = spec_of((10.0, 30.0))
        D.set_pair_layout(sp, 'a', 'b', 'y')
        S.set_mirror_at(sp, ('a', 'b'), -1300.0)
        D.set_pair_layout(sp, 'a', 'b', 'x')
        for n in ('a', 'b'):
            pc = D.piece(sp, n)
            self.assertNotIn('mirror_plane', pc)
            self.assertNotIn(S.MIRROR_LAYOUT, pc)
            self.assertNotIn(S.MIRROR_AT, pc)


if __name__ == '__main__':
    unittest.main()
