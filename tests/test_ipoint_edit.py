# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal lines' points as corners: picked one by one, moved by a number (alone, several, with other pieces' edges),
lined up across and up and down while dragged, smooth or sharp (user 2026-11-17).

Run: blender -b --factory-startup --python tests/test_ipoint_edit.py -- <parent of ac9_cloth_sim> [out.json]
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
from ac9_cloth_sim.core import spec as S, draft as DR, iline as IL, snap as SN  # noqa: E402

RESULTS = {}
RECT = [[50, 100], [150, 100], [150, 160], [50, 160]]


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


def verts(spec, name, lid):
    return IL.shape(DR.internal(DR.piece(spec, name), lid))[0].copy()


def clear_pick():
    ctx = bpy.context
    DT.select_corner(ctx)
    DT.select_edge(ctx)
    DT.select_internal(ctx)
    DT.select_ipoint(ctx)


def collinear(it, k):
    """|sin| of the angle between the two handles at vertex k (0: on one line)."""
    N, C, closed, _ = IL.shape(it)
    n = len(N)
    a = N[k] - (C[(k - 1) % n][1] if C[(k - 1) % n] is not None else N[(k - 1) % n])
    b = (C[k][0] if C[k] is not None else N[(k + 1) % n]) - N[k]
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    return float(abs(a[0] * b[1] - a[1] * b[0])) + (0.0 if a @ b > 0 else 1.0)


class IPointCore(unittest.TestCase):
    """No pick: the core edits."""

    def test_move_nodes_one_vertex(self):
        spec = S.new_spec('t')
        a = rect(spec, 'A', 0)
        lid = IL.add_shape(spec, a, np.array(RECT, float), [None] * 4)
        V0 = verts(spec, a, lid)
        IL.move_nodes(spec, a, lid, {2: (10.0, 5.0)})
        d = verts(spec, a, lid) - V0
        RESULTS['move_nodes'] = d.round(4).tolist()
        np.testing.assert_allclose(d[2], [10, 5], atol=1e-6)
        np.testing.assert_allclose(np.delete(d, 2, 0), 0, atol=1e-6)

    def test_smooth(self):
        spec = S.new_spec('t')
        a = rect(spec, 'A', 0)
        N = np.array(RECT, float)
        C = [np.array([[80, 90], [120, 90]]), np.array([[160, 120], [160, 140]]), None, None]   # a kink at 1
        lid = IL.add_shape(spec, a, N, C)
        it = DR.internal(DR.piece(spec, a), lid)
        before = collinear(it, 1)
        IL.set_smooth(spec, a, lid, 1, True)
        it = DR.internal(DR.piece(spec, a), lid)
        on = collinear(it, 1)
        # a handle dragged: the one across the vertex turns with it
        IL.set_handle(spec, a, lid, 1, 0, (170.0, 115.0))
        it = DR.internal(DR.piece(spec, a), lid)
        after_drag = collinear(it, 1)
        kept = IL.shape(it)[1][1][0].tolist()
        # a vertex added elsewhere: the smooth vertex is still the one at (150, 100)
        IL.insert_node(spec, a, lid, (100.0, 160.0))
        it = DR.internal(DR.piece(spec, a), lid)
        Nn = IL.shape(it)[0]
        sm = sorted(IL.smooth_nodes(it))
        IL.set_smooth(spec, a, lid, sm[0], False)
        off = IL.smooth_nodes(DR.internal(DR.piece(spec, a), lid))
        RESULTS['smooth'] = {'kink_before': before, 'after_on': on, 'after_handle_drag': after_drag,
                             'dragged_handle': kept, 'smooth_after_insert': [Nn[k].tolist() for k in sm],
                             'after_off': sorted(off)}
        self.assertGreater(before, 0.1)
        self.assertLess(on, 1e-4)          # the stored coordinates are rounded (D._r)
        self.assertLess(after_drag, 1e-4)
        np.testing.assert_allclose(kept, [170.0, 115.0], atol=1e-6)    # the dragged one stays where it was put
        self.assertEqual([Nn[k].tolist() for k in sm], [[150.0, 100.0]])
        self.assertEqual(off, set())


class IPointPick(unittest.TestCase):
    def setUp(self):
        spec = S.new_spec('IPoint')
        self.a = rect(spec, 'A', 0)
        self.b = rect(spec, 'B', 400)
        self.lid = IL.add_shape(spec, self.a, np.array(RECT, float), [None] * 4)
        self.spec = load(spec)
        clear_pick()

    def key(self, k):
        return DT.ipoint_key(self.lid, k)

    def test_one_point_by_number(self):
        V0 = verts(self.spec, self.a, self.lid)
        r = bpy.ops.ac9_cloth_sim.set_gap(kind='IPOINT', piece=self.a, thing=self.key(2), move_x=0.010, move_y=0.005)
        d = verts(now(), self.a, self.lid) - V0
        RESULTS['one_point'] = {'ret': list(r), 'moved': d.round(4).tolist()}
        self.assertEqual(r, {'FINISHED'})
        np.testing.assert_allclose(d[2], [10, 5], atol=1e-6)
        np.testing.assert_allclose(np.delete(d, 2, 0), 0, atol=1e-6)
        gl = DT.gap_list(self.spec, ('inode', self.a, self.lid, 2))
        RESULTS['point_gaps'] = [round(g[2], 2) for g in gl]
        self.assertTrue(gl)

    def test_two_points_and_an_edge_of_another_piece(self):
        V0 = verts(self.spec, self.a, self.lid)
        B0 = DR.corners(DR.piece(self.spec, self.b)).copy()
        items = [['ipoint', self.a, self.key(1)], ['ipoint', self.a, self.key(2)],
                 ['edge', self.b, DR.piece(self.spec, self.b)['edges'][1]['id']]]
        r = bpy.ops.ac9_cloth_sim.set_gap(kind='MULTI', items=json.dumps(items), move_x=0.030)
        sp = now()
        d = verts(sp, self.a, self.lid) - V0
        dB = DR.corners(DR.piece(sp, self.b)) - B0
        RESULTS['multi'] = {'ret': list(r), 'line': d.round(4).tolist(), 'B': dB.round(4).tolist()}
        np.testing.assert_allclose(d[[1, 2]], [[30, 0]] * 2, atol=1e-6)      # the rectangle 30 mm wider
        np.testing.assert_allclose(d[[0, 3]], 0, atol=1e-6)
        np.testing.assert_allclose(dB[[1, 2]], [[30, 0]] * 2, atol=1e-6)

    def test_pick_tab_menu_box(self):
        ctx = bpy.context
        DT.select_ipoint(ctx, self.a, self.lid, 2)
        sp = now()
        RESULTS['picked'] = DT.picked_lines(sp)
        self.assertEqual(DT.picked_lines(sp), [('ipoint', self.a, self.key(2))])
        RESULTS['tab'] = TT.resolve(ctx, sp)
        self.assertEqual(RESULTS['tab'], 'EDGES')
        rows = DT.menu_items(sp, ('inode', self.a, self.lid, 2))
        RESULTS['menu'] = [r[0] for r in rows if r]
        self.assertEqual(rows[0][0], DT.NUMERIC_INPUT)
        self.assertEqual(rows[0][3]['kind'], 'IPOINT')
        self.assertIn("Smooth Corner", RESULTS['menu'])
        # the point under the cursor is the thing a click picks and a drag moves (not its line)
        self.assertEqual(DT.edit_item(sp, ('inode', self.a, self.lid, 2)), ('ipoint', self.a, self.key(2)))

    def test_align_targets_and_snap(self):
        sp = self.spec
        A = DT.ialign_points(sp, ('inode', self.a, self.lid, 2))
        V = verts(sp, self.a, self.lid)
        # dragged 3 mm off: the other points of its line and the piece's corners are what it lines up with
        p = V[2] + np.array([2.0, -3.0])
        shift, lines = SN.align_snap(p, A, 5.0)
        RESULTS['align'] = {'targets': len(A), 'shift': np.asarray(shift).round(4).tolist(),
                            'lines': [[int(ax), int(j)] for ax, i, j in lines]}
        self.assertFalse(any(np.allclose(t, V[2]) for t in A))           # not itself
        np.testing.assert_allclose(p + shift, V[2], atol=1e-6)           # back in line with points 1 and 3

    def test_twin_once(self):
        spec = S.loads(S.dumps(self.spec))
        tw = DR.mirror_copy(spec, self.a, plane='x')
        self.spec = load(spec)
        tl, tlid, _ = DR._twin_internal(self.spec, self.a, self.lid)
        self.assertEqual(tl, tw)
        V0, T0 = verts(self.spec, self.a, self.lid), verts(self.spec, tw, tlid)
        M = next(m for nm, l2, m in DR.mirror_lines(self.spec, self.a, self.lid) if nm == tw)
        j = int(np.linalg.norm(T0 - np.asarray(M(V0), float)[2], axis=1).argmin())
        items = [['ipoint', self.a, self.key(2)], ['ipoint', tw, DT.ipoint_key(tlid, j)]]
        bpy.ops.ac9_cloth_sim.set_gap(kind='MULTI', items=json.dumps(items), move_y=-0.020)
        sp = now()
        dA, dT = verts(sp, self.a, self.lid) - V0, verts(sp, tw, tlid) - T0
        RESULTS['twin_once'] = {'A': dA[2].round(4).tolist(), 'twin': dT[j].round(4).tolist()}
        np.testing.assert_allclose(dA[2], [0, -20], atol=1e-6)
        np.testing.assert_allclose(dT[j], [0, -20], atol=1e-6)

    def test_rotate_points(self):
        sp = S.loads(S.dumps(self.spec))
        V0 = verts(sp, self.a, self.lid)
        c = V0[[1, 2]].mean(0)
        fm = DR.group_map(90.0, (1.0, 1.0), center=c)
        DT.move_ipoints(sp, [('ipoint', self.a, self.key(1)), ('ipoint', self.a, self.key(2))], set(),
                        lambda p: fm(p)[0] - p)
        V = verts(sp, self.a, self.lid)
        RESULTS['rotate'] = V.round(3).tolist()
        np.testing.assert_allclose(V[[0, 3]], V0[[0, 3]], atol=1e-6)
        np.testing.assert_allclose(np.linalg.norm(V[1] - V[2]), np.linalg.norm(V0[1] - V0[2]), atol=1e-6)
        np.testing.assert_allclose(V[[1, 2]].mean(0), c, atol=1e-6)


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('__main__'))
    if out:
        Path(out).write_text(json.dumps({'ok': res.wasSuccessful(), 'failures': [str(f[1]) for f in res.failures],
                                         'errors': [str(e[1]) for e in res.errors], 'results': RESULTS},
                                        indent=1, default=str), encoding='utf-8')
    sys.exit(0 if res.wasSuccessful() else 1)
