# SPDX-License-Identifier: GPL-3.0-or-later
"""Numeric Input on a whole piece and on a closed internal shape: the width and height of its bounding box typed.

A rectangle piece becomes 120 x 45 mm about its middle; an ellipse piece the same (its diameters); a piece edited
symmetrically stays symmetric; a pair's twin follows as its mirror image; a closed internal rectangle gets its size
and stays inside; moving by X / Y with the size unchanged still works; the right-click rows of a piece and of a
closed shape are named from the one constant (NUMERIC_INPUT) and translated.

Run: blender -b --factory-startup --python tests/test_numeric_size.py -- <parent of ac9_cloth_sim> [out.json]
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
from ac9_cloth_sim import draft_tool as DT, pattern_view as PV, store as ST, i18n_ja  # noqa: E402
from ac9_cloth_sim.core import spec as S, draft as DR        # noqa: E402
from ac9_cloth_sim import shape_tool as ST2, toolbar as TB   # noqa: E402

RESULTS = {}


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


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


def box(A):
    A = np.asarray(A, float)
    return A.max(0) - A.min(0), 0.5 * (A.max(0) + A.min(0))


def size_of(spec, name):
    return box(DR.corners_all(DR.piece(spec, name)))


def run(kind, piece, thing='', w=None, h=None, mx=0.0, my=0.0, spec=None):
    """The operator as its dialog's OK runs it: invoke's starting values set by hand (EXEC has no invoke)."""
    sp = spec or now()
    if kind == 'PIECE':
        w0, h0 = size_of(sp, piece)[0] / 1000.0
    else:
        w0, h0 = box(DR.internal(DR.piece(sp, piece), thing)['pts'])[0] / 1000.0
    return bpy.ops.ac9_cloth_sim.set_gap('EXEC_DEFAULT', kind=kind, piece=piece, thing=thing,
                                         size0=(w0, h0), width=w0 if w is None else w,
                                         height=h0 if h is None else h, move_x=mx, move_y=my)


class NumericSize(unittest.TestCase):
    def test_rectangle_piece(self):
        spec = S.new_spec('NS')
        a = DR.add_polygon(spec, DR.rect_points(np.array([0.0, 0.0]), np.array([200.0, 300.0])), "Rect")
        load(spec)
        r = run('PIECE', a, w=0.120, h=0.045)
        (W, c) = size_of(now(), a)
        RESULTS['rect'] = {'ret': list(r), 'size': W.round(3).tolist(), 'center': c.round(3).tolist()}
        self.assertEqual(r, {'FINISHED'})
        np.testing.assert_allclose(W, [120, 45], atol=0.01)
        np.testing.assert_allclose(c, [100, 150], atol=0.01)   # about its middle

    def test_ellipse_piece(self):
        spec = S.new_spec('NS')
        a = DR.add_ellipse(spec, np.array([0.0, 0.0]), 50.0, 30.0)
        load(spec)
        r = run('PIECE', a, w=0.080, h=0.080)
        (W, c) = size_of(now(), a)
        RESULTS['ellipse'] = {'ret': list(r), 'size': W.round(3).tolist()}
        self.assertEqual(r, {'FINISHED'})
        np.testing.assert_allclose(W, [80, 80], atol=0.05)

    def test_symmetric_piece(self):
        spec = S.new_spec('NS')
        a = DR.add_polygon(spec, [[0, 0], [200, 0], [160, 300], [40, 300]], "Trap")
        DR.piece(spec, a)['mirror_edit'] = True
        load(spec)
        r = run('PIECE', a, w=0.150, h=0.200)
        pc = DR.piece(now(), a)
        RESULTS['sym'] = {'ret': list(r), 'size': size_of(now(), a)[0].round(3).tolist(),
                          'still_symmetric': DR.symmetric_edit(pc) is not None}
        self.assertEqual(r, {'FINISHED'})
        self.assertIsNotNone(DR.symmetric_edit(pc))
        np.testing.assert_allclose(size_of(now(), a)[0], [150, 200], atol=0.01)

    def test_twin_follows(self):
        spec = S.new_spec('NS')
        a = DR.add_polygon(spec, [[0, 0], [200, 0], [180, 300], [0, 300]], "L")
        DR.mirror_copy(spec, a)
        sp = load(spec)
        tw = DR.twin_of(sp, a)
        self.assertIsNotNone(tw)
        r = run('PIECE', a, w=0.100, h=0.150, spec=sp)
        sp2 = now()
        RESULTS['twin'] = {'ret': list(r), 'a': size_of(sp2, a)[0].round(3).tolist(),
                           'twin': size_of(sp2, tw)[0].round(3).tolist()}
        self.assertEqual(r, {'FINISHED'})
        np.testing.assert_allclose(size_of(sp2, tw)[0], [100, 150], atol=0.01)

    def test_closed_internal_shape(self):
        spec = S.new_spec('NS')
        a = DR.add_polygon(spec, DR.rect_points(np.array([0.0, 0.0]), np.array([200.0, 300.0])), "Rect")
        lid = DR.add_internal(spec, a, DR.rect_points(np.array([50.0, 100.0]), np.array([110.0, 140.0])),
                              kind='shape', closed=True)
        load(spec)
        r = run('ILINE', a, lid, w=0.030, h=0.070)
        W, c = box(DR.internal(DR.piece(now(), a), lid)['pts'])
        RESULTS['shape'] = {'ret': list(r), 'size': W.round(3).tolist(), 'center': c.round(3).tolist()}
        self.assertEqual(r, {'FINISHED'})
        np.testing.assert_allclose(W, [30, 70], atol=0.01)
        np.testing.assert_allclose(c, [80, 120], atol=0.01)

    def test_shape_move_only(self):
        spec = S.new_spec('NS')
        a = DR.add_polygon(spec, DR.rect_points(np.array([0.0, 0.0]), np.array([200.0, 300.0])), "Rect")
        lid = DR.add_internal(spec, a, DR.rect_points(np.array([50.0, 100.0]), np.array([110.0, 140.0])),
                              kind='shape', closed=True)
        load(spec)
        r = run('ILINE', a, lid, mx=0.010)
        W, c = box(DR.internal(DR.piece(now(), a), lid)['pts'])
        RESULTS['shape_move'] = {'ret': list(r), 'size': W.round(3).tolist(), 'center': c.round(3).tolist()}
        self.assertEqual(r, {'FINISHED'})
        np.testing.assert_allclose(W, [60, 40], atol=0.01)
        np.testing.assert_allclose(c, [90, 120], atol=0.01)

    def test_menu_rows_named(self):
        spec = S.new_spec('NS')
        a = DR.add_polygon(spec, DR.rect_points(np.array([0.0, 0.0]), np.array([200.0, 300.0])), "Rect")
        lid = DR.add_internal(spec, a, DR.rect_points(np.array([50.0, 100.0]), np.array([110.0, 140.0])),
                              kind='shape', closed=True)
        sp = load(spec)
        rows_p = [r for r in DT.menu_items(sp, ('piece', a)) if r]
        rows_s = [r for r in DT.menu_items(sp, ('iline', a, lid)) if r]
        RESULTS['menu'] = {'piece': [r[0] for r in rows_p], 'shape': [r[0] for r in rows_s]}
        self.assertEqual(DT.NUMERIC_INPUT, "Numeric Input...")
        self.assertEqual(rows_p[0][0], DT.NUMERIC_INPUT)
        self.assertEqual(rows_p[0][3]['kind'], 'PIECE')
        self.assertIn(DT.NUMERIC_INPUT, [r[0] for r in rows_s])
        self.assertEqual(i18n_ja.JA.get(DT.NUMERIC_INPUT), "数値入力…")
        # the drawing tools' right-click (user 2026-11-17: not Blender's object menu): Numeric Input alone
        draw_p = DT.menu_items(sp, ('piece', a), 'DRAW')
        draw_s = DT.menu_items(sp, ('iline', a, lid), 'DRAW')
        RESULTS['menu_draw'] = {'piece': [r[0] for r in draw_p], 'shape': [r[0] for r in draw_s]}
        self.assertEqual([r[0] for r in draw_p], [DT.NUMERIC_INPUT])
        self.assertEqual(draw_p[0][3]['kind'], 'PIECE')
        self.assertEqual([r[0] for r in draw_s], [DT.NUMERIC_INPUT])
        self.assertEqual(draw_s[0][3]['kind'], 'ILINE')



class DrawToolsMenuKey(unittest.TestCase):
    def test_right_click_bound(self):
        tools = [ST2.AC9_WT_Rect, ST2.AC9_WT_Ellipse, ST2.AC9_WT_IRect, ST2.AC9_WT_IEllipse, TB.AC9_WT_DrawPiece,
                 TB.AC9_WT_DrawLine]
        got = {t.bl_idname: any(k[0] == 'ac9_cloth_sim.context_menu' and k[1].get('type') == 'RIGHTMOUSE'
                                for k in t.bl_keymap) for t in tools}
        RESULTS['menu_key'] = got
        self.assertTrue(all(got.values()))


if __name__ == '__main__':
    res = unittest.main(argv=['x'], exit=False, verbosity=2).result
    if len(ARGS) > 1:
        RESULTS['ok'] = res.wasSuccessful()
        Path(ARGS[1]).write_text(json.dumps(RESULTS, indent=1, default=str), encoding='utf-8')
    sys.exit(0 if res.wasSuccessful() else 1)
