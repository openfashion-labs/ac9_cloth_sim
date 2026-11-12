"""Slash and spread along the center line of a piece edited symmetrically keeps it symmetric (no Blender)."""
import importlib.util
import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_spread_center_test'
definition = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(definition)
sys.modules[PACKAGE] = package
definition.loader.exec_module(package)
S = __import__(PACKAGE + '.core.spec', fromlist=['spec'])
D = __import__(PACKAGE + '.core.draft', fromlist=['draft'])
SP = __import__(PACKAGE + '.core.spread', fromlist=['spread'])


def _piece(center_line=True):
    spec = S.new_spec('test')
    name = D.add_polygon(spec, [(0, 0), (400, 0), (400, 600), (0, 600)], name='A')
    name = name if isinstance(name, str) else 'A'
    pc = D.piece(spec, name)
    pc['mirror_edit'] = True
    D._store_axis(pc)
    x = 200.0 if center_line else 120.0
    D.add_internal(spec, name, [(x, 600.0), (x, 0.0)], mirror=False)
    lid = pc['internals'][-1]['id']
    return spec, name, lid


class SpreadCenter(unittest.TestCase):
    def _check(self, mode):
        spec, name, lid = _piece()
        axis0 = list(D.piece(spec, name)['mirror_axis'])
        out = SP.spread(spec, name, lid, 30.0, mode, 'start', 'auto')
        pc = D.piece(spec, name)
        self.assertGreater(out[name]['area_added'], 1000.0)
        self.assertEqual(pc['mirror_axis'], axis0)
        self.assertLess(SP._asymmetry(pc), 0.05)
        self.assertIsNotNone(D.self_mirror_map(pc))

    def test_flare(self):
        self._check('hinge')

    def test_gathers(self):
        self._check('parallel')


class SpreadCenterNoSymmetricEdit(unittest.TestCase):
    """A symmetric piece with Symmetric Edit off, spread along its center line: both halves open alike (user
    2026-11-11: one half opened by the whole amount, a corner 31.6 mm off mirroring)."""

    def _spread(self, x, mode, side='auto'):
        spec = S.new_spec('test')
        name = D.add_polygon(spec, [(0, 0), (400, 0), (400, 600), (0, 600)], name='A')
        D.add_internal(spec, name, [(x, 600.0), (x, 0.0)], mirror=False)
        lid = D.piece(spec, name)['internals'][-1]['id']
        SP.spread(spec, name, lid, 30.0, mode, 'start', side)
        return SP._asymmetry_about(D.corners(D.piece(spec, name)), np.array([200.0, 0.0]), np.array([0.0, 1.0]),
                                   worst=True)

    def test_flare(self):
        self.assertLess(self._spread(200.0, 'hinge'), 0.05)

    def test_gathers(self):
        self.assertLess(self._spread(200.0, 'parallel'), 0.05)

    def test_side_picked_moves_that_side(self):
        self.assertGreater(self._spread(200.0, 'hinge', 'a'), 10.0)     # a side asked: only it turns

    def test_off_center_line(self):
        self.assertGreater(self._spread(120.0, 'hinge'), 10.0)          # not the center: one side turns


class LineEndsRide(unittest.TestCase):
    """An internal line drawn from outline to outline stays on the outline when an edge it ends on moves."""

    def test_edge_moved_out(self):
        spec = S.new_spec('test')
        name = D.add_polygon(spec, [(0, 0), (400, 0), (400, 600), (0, 600)], name='A')
        D.add_internal(spec, name, [(0.0, 300.0), (400.0, 300.0)], mirror=False)
        pc = D.piece(spec, name)
        it = pc['internals'][-1]
        right = next(e['id'] for e in pc['edges'] if abs(e['pts'][0][0] - 400) < 1e-6 and abs(e['pts'][-1][0] - 400) < 1e-6)
        D.move_edge(spec, name, right, (50.0, 0.0), twins=False)
        P = np.asarray(it['pts'], float)
        self.assertAlmostEqual(P[0][0], 0.0, places=3)        # the end on the edge that stayed: still
        self.assertAlmostEqual(P[-1][0], 450.0, places=3)     # the end on the moved edge: on it
        self.assertAlmostEqual(P[-1][1], 300.0, places=3)

    def test_corner_moved(self):
        spec = S.new_spec('test')
        name = D.add_polygon(spec, [(0, 0), (400, 0), (400, 600), (0, 600)], name='A')
        D.add_internal(spec, name, [(0.0, 300.0), (400.0, 300.0)], mirror=False)
        pc = D.piece(spec, name)
        it = pc['internals'][-1]
        k = int(np.linalg.norm(D.corners(pc) - [400, 600], axis=1).argmin())
        D.move_corners(spec, name, {k: (100.0, 0.0)}, twins=False)
        q = np.asarray(it['pts'][-1], float).reshape(1, 2)
        edge = next(np.asarray(e['pts'], float) for e in pc['edges'] if np.allclose(e['pts'][-1], [500, 600]) or np.allclose(e['pts'][0], [500, 600]))
        self.assertLess(float(D._dist_to_polyline(q, edge)[0]), 1e-3)       # on the slanted edge
        self.assertAlmostEqual(float(it['pts'][-1][0]), 450.0, places=3)     # half way: the edge's middle


if __name__ == '__main__':
    unittest.main()
