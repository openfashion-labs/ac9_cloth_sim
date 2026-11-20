# SPDX-License-Identifier: GPL-3.0-or-later
"""Numeric Input on corners and on several picked things (edges, corners, internal lines, across pieces).

One corner moves by X / Y and has gaps to offer; the bottom edges of two pieces move down together; an edge picked
with its own corner moves once; a pair's two sides picked together move once each (not twice); the Tool tab shows
AC9 Edges for picked corners and the right-click menu has the row (the picked things together when the clicked one
is among them).

Run: blender -b --factory-startup --python tests/test_move_by_number.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import numpy as np                                           # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import draft_tool as DT, pattern_view as PV, store as ST, tool_tab as TT  # noqa: E402
from ac9_cloth_sim.core import spec as S, draft as DR        # noqa: E402

RESULTS = {}


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def rect(spec, name, x0, w=200.0, h=300.0):
    return DR.add_polygon(spec, [[x0, 0], [x0 + w, 0], [x0 + w, h], [x0, h]], name=name)


def load(spec):
    ctx = bpy.context
    if ST.text(ctx) is None:
        ST.new_text(ctx, spec['name'])
    ST.text(ctx).clear()
    ST.text(ctx).write(S.dumps(spec))
    ST.invalidate()
    sp, outlines = ST.get(ctx)
    PV.rebuild(ctx, sp, outlines)
    return sp


def now():
    ST.invalidate()
    return ST.get(bpy.context)[0]


def corners(spec, name):
    return DR.corners(DR.piece(spec, name)).copy()


def clear_pick():
    ctx = bpy.context
    DT.select_corner(ctx)
    DT.select_edge(ctx)
    DT.select_internal(ctx)


class MoveByNumber(unittest.TestCase):
    def setUp(self):
        spec = S.new_spec('MoveByNumber')
        self.a = rect(spec, 'A', 0)
        self.b = rect(spec, 'B', 400)
        self.lid = DR.add_internal(spec, self.a, [[50, 100], [150, 100]])
        self.spec = load(spec)
        clear_pick()

    def eid(self, name, k):
        return DR.piece(self.spec, name)['edges'][k]['id']

    def test_one_corner(self):
        C0 = corners(self.spec, self.a)
        r = bpy.ops.ac9_cloth_sim.set_gap(kind='CORNER', piece=self.a, thing=self.eid(self.a, 2), move_x=0.010,
                                          move_y=0.005)
        d = corners(now(), self.a) - C0
        RESULTS['one_corner'] = {'ret': list(r), 'moved': d.round(4).tolist()}
        self.assertEqual(r, {'FINISHED'})
        np.testing.assert_allclose(d[2], [10, 5], atol=1e-6)
        np.testing.assert_allclose(np.delete(d, 2, 0), 0, atol=1e-6)
        # a corner has gaps to offer (the probe takes it)
        gl = DT.gap_list(self.spec, ('corner', self.a, 2))
        RESULTS['corner_gaps'] = len(gl)
        self.assertTrue(gl)

    def test_two_bottoms_across_pieces(self):
        A0, B0 = corners(self.spec, self.a), corners(self.spec, self.b)
        items = [['edge', self.a, self.eid(self.a, 0)], ['edge', self.b, self.eid(self.b, 0)]]
        r = bpy.ops.ac9_cloth_sim.set_gap(kind='MULTI', items=json.dumps(items), move_y=-0.030)
        sp = now()
        dA, dB = corners(sp, self.a) - A0, corners(sp, self.b) - B0
        RESULTS['two_bottoms'] = {'ret': list(r), 'A': dA.round(4).tolist(), 'B': dB.round(4).tolist()}
        self.assertEqual(r, {'FINISHED'})
        for d in (dA, dB):
            np.testing.assert_allclose(d[:2], [[0, -30]] * 2, atol=1e-6)     # the bottom corners
            np.testing.assert_allclose(d[2:], 0, atol=1e-6)                  # the top ones stay

    def test_edge_with_its_corner_moves_once(self):
        A0 = corners(self.spec, self.a)
        items = [['edge', self.a, self.eid(self.a, 0)], ['corner', self.a, self.eid(self.a, 0)],
                 ['iline', self.a, self.lid]]
        P0 = np.asarray(DR.internal(DR.piece(self.spec, self.a), self.lid)['pts'], float)
        r = bpy.ops.ac9_cloth_sim.set_gap(kind='MULTI', items=json.dumps(items), move_x=0.020)
        sp = now()
        d = corners(sp, self.a) - A0
        dP = np.asarray(DR.internal(DR.piece(sp, self.a), self.lid)['pts'], float) - P0
        RESULTS['edge_and_corner'] = {'ret': list(r), 'A': d.round(4).tolist(), 'line': dP.round(4).tolist()}
        np.testing.assert_allclose(d[:2], [[20, 0]] * 2, atol=1e-6)
        np.testing.assert_allclose(dP, [[20, 0]] * 2, atol=1e-6)

    def test_pair_both_sides_once(self):
        spec = S.loads(S.dumps(self.spec))
        tw = DR.mirror_copy(spec, self.a, plane='x')
        self.spec = load(spec)
        A0, T0 = corners(self.spec, self.a), corners(self.spec, tw)
        _, cmap, _ = DR.twin_corner_map(self.spec, self.a)
        k_tw = cmap[0]                                        # the twin's corner mirroring A's corner 0
        items = [['corner', self.a, self.eid(self.a, 0)], ['corner', tw, self.eid(tw, k_tw)]]
        r = bpy.ops.ac9_cloth_sim.set_gap(kind='MULTI', items=json.dumps(items), move_y=-0.030)
        sp = now()
        dA, dT = corners(sp, self.a) - A0, corners(sp, tw) - T0
        RESULTS['pair_once'] = {'ret': list(r), 'A': dA[0].round(4).tolist(), 'twin': dT[k_tw].round(4).tolist()}
        np.testing.assert_allclose(dA[0], [0, -30], atol=1e-6)
        np.testing.assert_allclose(dT[k_tw], [0, -30], atol=1e-6)
        # the ghost's copy leaves the stored twin alone (its pieces copied with the twins)
        sp0 = now()
        T1 = corners(sp0, tw)
        cp = DT.light_copy(sp0, DT._touched(sp0, [('corner', self.a, self.eid(self.a, 0))]))
        DT.move_lines(cp, [('corner', self.a, self.eid(self.a, 0))], (0.0, 7.0))
        RESULTS['ghost_twin_untouched'] = float(np.abs(corners(sp0, tw) - T1).max())
        self.assertLess(RESULTS['ghost_twin_untouched'], 1e-9)

    def test_tool_tab_and_menu(self):
        ctx = bpy.context
        DT.select_corner(ctx, self.a, self.eid(self.a, 2))
        RESULTS['tab_one_corner'] = TT.resolve(ctx, now())
        self.assertEqual(RESULTS['tab_one_corner'], 'EDGES')
        rows = DT.menu_items(now(), ('corner', self.a, 2))
        first = rows[0]
        RESULTS['menu_one'] = [first[0], first[3].get('kind')]
        self.assertEqual(first[0], DT.NUMERIC_INPUT)
        self.assertEqual(first[3]['kind'], 'CORNER')
        DT.select_edge(ctx, self.b, self.eid(self.b, 0), extend=True)
        rows = DT.menu_items(now(), ('corner', self.a, 2))
        RESULTS['menu_multi'] = [rows[0][0], rows[0][3].get('kind'), len(json.loads(rows[0][3]['items']))]
        self.assertEqual(rows[0][3]['kind'], 'MULTI')
        # an edge not picked: that edge alone
        rows = DT.menu_items(now(), ('edge', self.b, self.eid(self.b, 2)))
        self.assertEqual(rows[0][3]['kind'], 'EDGE')
        # an internal line under the cursor (not picked)
        rows = DT.menu_items(now(), ('iline', self.a, self.lid))
        RESULTS['menu_iline'] = [r[0] for r in rows if r]
        self.assertIn(DT.NUMERIC_INPUT, RESULTS['menu_iline'])


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('__main__'))
    if out:
        Path(out).write_text(json.dumps({'ok': res.wasSuccessful(), 'failures': [str(f[1]) for f in res.failures],
                                         'errors': [str(e[1]) for e in res.errors], 'results': RESULTS}, indent=1),
                             encoding='utf-8')
    sys.exit(0 if res.wasSuccessful() else 1)
