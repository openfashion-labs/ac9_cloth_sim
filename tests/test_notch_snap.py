"""Where a notch snaps on the flat pattern: an edge's middle and where the partner's notch falls, not a corner (no
Blender)."""
import importlib.util
import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_notch_snap_test'
definition = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(definition)
sys.modules[PACKAGE] = package
definition.loader.exec_module(package)
S = __import__(PACKAGE + '.core.spec', fromlist=['spec'])
D = __import__(PACKAGE + '.core.draft', fromlist=['draft'])


def _bottom(pc):
    return next(e['id'] for e in pc['edges'] if abs(e['pts'][0][1]) < 1e-6 and abs(e['pts'][-1][1]) < 1e-6)


def _pair():
    """A (400 wide) sewn along its bottom to B's bottom (600 wide), the sides running the same way."""
    spec = S.new_spec('test')
    a = D.add_polygon(spec, [(0, 0), (400, 0), (400, 300), (0, 300)], name='A')
    b = D.add_polygon(spec, [(1000, 0), (1600, 0), (1600, 300), (1000, 300)], name='B')
    ea, eb = _bottom(D.piece(spec, a)), _bottom(D.piece(spec, b))
    spec['seams'].append({'id': 'S1', 'source': 'user', 'check': '',
                          'a': {'piece': a, 'from': [ea, 0.0], 'to': [ea, 1.0], 'dir': 1},
                          'b': {'piece': b, 'from': [eb, 0.0], 'to': [eb, 1.0], 'dir': 1}})
    return spec, a, b, ea, eb


def _x(spec, name, eid, t):
    ol = S.Outline(D.piece(spec, name))
    return float(ol.at(ol.s_of(eid, t))[0])


class NotchSnap(unittest.TestCase):
    def test_edge_middle(self):
        spec, a, b, ea, eb = _pair()
        e, t, kind = D.snap_notch(spec, a, ea, 0.48, 10.0)          # 192 mm: 8 mm off the middle
        self.assertEqual(kind, 'mid')
        self.assertAlmostEqual(_x(spec, a, e, t), 200.0, places=3)

    def test_partner_notch(self):
        spec, a, b, ea, eb = _pair()
        D.add_notch(spec, b, eb, 0.25, twins=False)                   # B at 150 of 600: A's place 100 of 400
        e, t, kind = D.snap_notch(spec, a, ea, 0.26, 10.0)          # 104 mm
        self.assertEqual(kind, 'partner')
        self.assertAlmostEqual(_x(spec, a, e, t), 100.0, places=3)

    def test_not_a_corner(self):
        spec, a, b, ea, eb = _pair()
        e, t, kind = D.snap_notch(spec, a, ea, 0.01, 10.0)          # 4 mm from the corner
        self.assertIsNone(kind)
        self.assertAlmostEqual(t, 0.01)


if __name__ == '__main__':
    unittest.main()
