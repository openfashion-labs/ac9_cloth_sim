"""Turning a placed piece on the pattern does not turn it on the body: the placement keeps the turn ('spin') and the
layout points as placed are turned back (core/spec.spun; user 2026-11-11). No Blender."""
import importlib.util
import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_layout_spin_test'
definition = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(definition)
sys.modules[PACKAGE] = package
definition.loader.exec_module(package)
S = __import__(PACKAGE + '.core.spec', fromlist=['spec'])
D = __import__(PACKAGE + '.core.draft', fromlist=['draft'])


def _placed():
    spec = S.new_spec('test')
    a = D.add_polygon(spec, [(0, 0), (400, 0), (400, 600), (0, 600)], name='A')
    spec['placement'][a] = {'guide': 'torso', 'theta': 0.0, 'h': 0.0, 'anchor': [200.0, 300.0]}
    return spec, a


def _as_placed(spec, name):
    pl = spec['placement'][name]
    return S.spun(pl, S.Outline(D.piece(spec, name)).B) - np.asarray(pl['anchor'], float)


class LayoutSpin(unittest.TestCase):
    def test_turn_and_move_keep_the_placed_shape(self):
        spec, a = _placed()
        before = _as_placed(spec, a)
        D.move_piece(spec, a, deg=35.0, shift=(120.0, -40.0))
        D.move_piece(spec, a, deg=-80.0)
        self.assertAlmostEqual(spec['placement'][a]['spin'], -45.0, places=6)
        np.testing.assert_allclose(_as_placed(spec, a), before, atol=1e-3)

    def test_group_turn(self):
        spec, a = _placed()
        before = _as_placed(spec, a)
        D.transform_group(spec, [a], (), 90.0, (1.0, 1.0), (0.0, 0.0))
        np.testing.assert_allclose(_as_placed(spec, a), before, atol=1e-3)

    def test_old_file_without_spin(self):
        spec, a = _placed()
        B = S.Outline(D.piece(spec, a)).B
        np.testing.assert_allclose(S.spun(spec['placement'][a], B), B)

    def test_inner_turn_is_not_kept(self):
        spec, a = _placed()
        D.transform_piece(spec, a, deg=30.0)                  # a turn the add-on makes itself (spread, symmetrize)
        self.assertNotIn('spin', spec['placement'][a])


def _rect_pts(w, h, step=50.0):
    """A w x h rectangle's outline, a point every step mm, counter-clockwise from (0, 0)."""
    out = []
    for a, b in (((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))):
        n = int(round(np.hypot(b[0] - a[0], b[1] - a[1]) / step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
    return np.array(out, float)


def _turn(P, deg, c):
    a = np.radians(deg)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return (np.asarray(P, float) - c) @ R.T + c


try:
    import bpy  # noqa: F401  (dress and overlay need Blender)
    HAVE_BPY = True
except ImportError:
    HAVE_BPY = False


@unittest.skipUnless(HAVE_BPY, "needs Blender")
class LayoutSpinRing(unittest.TestCase):
    """A front turned 90 deg on the pattern: the ring Dress finds round the torso and the radii the placement view
    wraps at are those of the unturned pattern (design review of the spin, 2026-11-11)."""

    def _ring(self, deg):
        DR = __import__(PACKAGE + '.dress', fromlist=['dress'])
        V = _rect_pts(400.0, 600.0)
        anchor = np.array([200.0, 300.0])
        left = np.where(np.abs(V[:, 0]) < 1e-6)[0]
        right = np.where(np.abs(V[:, 0] - 400.0) < 1e-6)[0]
        rows = [(0, i, 1, i) for i in left] + [(0, i, 1, i) for i in right]
        pat = {'V0': _turn(V, deg, anchor), 'V1': V.copy(), 'nb': np.array([len(V), len(V)]),
               'stitch': np.array(rows, int)}
        pl = {'F': {'guide': 'torso', 'theta': 0.0, 'h': 0.0, 'anchor': list(anchor), 'spin': deg},
              'B': {'guide': 'torso', 'theta': 180.0, 'h': 0.0, 'anchor': list(anchor)}}
        return DR._auto_ring(['F', 'B'], pl, pat, None)

    def test_auto_ring(self):
        ring0, wid0, _ = self._ring(0.0)
        ring1, wid1, _ = self._ring(90.0)
        self.assertEqual(sorted(ring0), ['B', 'F'])
        self.assertEqual(ring1, ring0)
        self.assertAlmostEqual(wid1, wid0, places=3)

    def test_wrap_radii(self):
        OV = __import__(PACKAGE + '.overlay', fromlist=['overlay'])
        spec, a = _placed()
        spec['guides'] = {'arm_L': {'o': [0.0, 0.0, 0.0], 'r': 50.0}}
        spec['placement'][a]['guide'] = 'arm_L'
        r0 = OV.wrap_radii(spec, {a: S.Outline(D.piece(spec, a))})
        D.move_piece(spec, a, deg=90.0)
        r1 = OV.wrap_radii(spec, {a: S.Outline(D.piece(spec, a))})
        self.assertAlmostEqual(r1[a], r0[a], places=3)


if __name__ == '__main__':
    unittest.main()
