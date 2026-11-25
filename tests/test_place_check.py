# SPDX-License-Identifier: GPL-3.0-or-later
"""blender -b --factory-startup --python tests/test_place_check.py -- <package parent> <result.json>

Diagnose (place_check) counts a vertex inside the body as the run's own start check does (solver/start_check.py):
deeper than INSIDE_MM by the nearest face's normal, within REACH_MM, faces on the body's open edges not judged. Settle's
measure counted any depth on every face, and on a test garment said 46 where the run said 34. The panel draws the
button and heading by the names in place_check, and the line it shows is the last press's record, stale once a piece
it looked at moves. The panel holds no how-to lines any more (they are in the tooltips and the status bar)."""
import ast
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
from ac9_cloth_sim import place_check as PCK, i18n_ja

ROOT = Path(__file__).resolve().parents[1]


def box(lo, hi, open_top=False):
    """Box (mm) as outward triangles; open_top leaves its top out (a hole, as a cropped body)."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    P = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    Q = [(0, 3, 2, 1), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)] + ([] if open_top else [(4, 5, 6, 7)])
    return np.array(P, float), np.array([t for a, b, c, d in Q for t in ((a, b, c), (a, c, d))])


def open_faces(T, n):
    E = np.sort(np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], 1)
    u, c = np.unique(E, axis=0, return_counts=True)
    b = np.zeros(n, bool)
    b[u[c == 1].ravel()] = True
    return b[T].any(1)


class PlaceCheck(unittest.TestCase):
    def setUp(self):
        self.co, self.tri = box((0, 0, 0), (400, 400, 400))
        self.bvh = BVHTree.FromPolygons((self.co * 0.001).tolist(), self.tri.tolist(), all_triangles=True)

    def test_depth_rule(self):
        # under the side x = 0 face: 0.3 mm in is contact, 1 mm in is inside; 60 mm in is past the reach
        X = np.array([[0.3, 200, 200], [1.0, 200, 200], [-5.0, 200, 200], [60.0, 200, 200]])
        self.assertEqual(PCK.inside(self.bvh, X, None).tolist(), [False, True, False, False])

    def test_open_faces_not_judged(self):
        co, tri = box((0, 0, 0), (400, 400, 400), open_top=True)
        bvh = BVHTree.FromPolygons((co * 0.001).tolist(), tri.tolist(), all_triangles=True)
        skip = open_faces(tri, len(co))
        X = np.array([[200, 1.0, 399.0]])            # near the hole's rim: its nearest face touches the open edge
        self.assertTrue(PCK.inside(bvh, X, None)[0])
        self.assertFalse(PCK.inside(bvh, X, skip)[0])

    def test_no_body(self):
        self.assertFalse(PCK.inside(None, np.zeros((3, 3)), None).any())

    def test_summary(self):
        f = {'a': {'inside': np.array([1, 1, 0], bool), 'cross_t': np.array([4]), 'stretch': 1.08},
             'b': {'inside': np.zeros(3, bool), 'cross_t': np.zeros(0, int), 'stretch': 1.31}}
        text, a = PCK.summary(f)
        self.assertEqual(text.format(*a), "Inside the body 2 (1 pieces), through cloth 1; stretch up to x1.31")
        f['a'] = {'inside': np.zeros(3, bool), 'cross_t': np.zeros(0, int), 'stretch': 1.0}
        text, a = PCK.summary(f)
        self.assertEqual(text.format(*a), "No problems (2 pieces); stretch up to x1.31")

    def test_spots_one_per_patch(self):
        # a strip of 6 vertices, 4 triangles: vertices 0 and 2 inside share no edge (two patches); 0 and 1 do (one)
        X = np.array([[0, 0, 0], [10, 0, 0], [20, 0, 0], [0, 10, 0], [10, 10, 0], [20, 10, 0]], float)
        T = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]])
        f = {'T': T, 'Y': X + [0, 0, 1000], 'V': X[:, :2] + 500, 'inside': np.array([1, 0, 1, 0, 0, 0], bool),
             'cross_t': np.zeros(0, np.int64)}
        self.assertEqual(sorted(s['in'] for s in PCK.spots('a', f)), [1, 1])
        f['inside'] = np.array([1, 1, 0, 0, 0, 0], bool)
        sp = PCK.spots('a', f)
        self.assertEqual([s['in'] for s in sp], [2])
        self.assertIn(sp[0]['c'], (0, 1))           # a vertex of its patch: on the cloth, the same in 3D and flat
        self.assertAlmostEqual(sp[0]['f'][0], X[sp[0]['c']][0] / 1000 + 0.5, 6)
        f['inside'][:] = False
        self.assertEqual(PCK.spots('a', f), [])

    def test_merge_by_distance_not_screen(self):
        # merged where the spots are (the count must not change with the view); the mark at the largest spot,
        # whatever order the spots come in
        from mathutils import Vector
        from ac9_cloth_sim import diagnose as DG
        S = [[Vector((0.003, 0, 0.6)), 3, 0, 'b'], [Vector((-0.003, 0, 0.6)), 7, 0, 'a'],
             [Vector((0.0, 0.05, 0.6)), 7, 0, 'c']]
        for order in (S, S[::-1], [S[2], S[0], S[1]]):
            m = DG.merge_spots(order)
            self.assertEqual(sorted((a, k) for _p, a, _b, k in m), [(7, 'c'), (10, 'a')])
            self.assertAlmostEqual(next(p for p, a, _b, _k in m if a == 10).x, -0.003, 6)

    def test_labels_clear(self):
        from ac9_cloth_sim import diagnose as DG
        marks = [((100, 100), True, "x"), ((160, 100), True, "x")]
        (x0, y0), (x1, y1) = DG.label_places(marks, lambda t: (60.0, 12.0))
        self.assertFalse(abs(y0 - y1) < 12 and x0 < x1 + 60 and x1 < x0 + 60)

    def test_names_from_one_place(self):
        src = (ROOT / 'ui.py').read_text(encoding='utf-8')
        self.assertIn('text=PCK.NAME', src)
        self.assertIn('class AC9_PT_SimDiagnosis', src)
        self.assertEqual(PCK.AC9_OT_SimDiagnosePlaced.bl_label, PCK.NAME)
        ja = i18n_ja.__dict__
        table = next(v for v in ja.values() if isinstance(v, dict) and 'Diagnose' in v)
        for en in (PCK.NAME, PCK.HEADING):
            self.assertIn(en, table)

    def test_panel_has_no_how_to_lines(self):
        tree = ast.parse((ROOT / 'ui.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AC9_PT_SimPlacement')
        calls = [n.func.id for n in ast.walk(cls) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn('draw_hint', calls)
        self.assertNotIn('show_hints', calls)


if __name__ == '__main__':
    ac9_cloth_sim.register()
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PlaceCheck))
    Path(args[1]).write_text(json.dumps(dict(passed=result.wasSuccessful(), tests=result.testsRun,
                                             log=log.getvalue()), indent=2), encoding='utf8')
    if not result.wasSuccessful():
        raise RuntimeError(log.getvalue())
