# SPDX-License-Identifier: GPL-3.0-or-later
"""The piece selection promise, row by row: a picked piece carries the same mark in every view it shows in, and
only marked pieces are acted on.

Each row is one state (pattern only / adjusted shape / dressed garment) x view x entry the tools call (the pattern
click, the placed click, the garment click, each with or without Shift). A row checks the logical selection and
the marks: the pattern objects' selection, the adjusted shapes' fill, the dressed garment's tint.

Rows whose behavior is not decided yet are skipped, not written down as today's behavior (it would turn today's
behavior into the spec). Rows that need Blender's own select operators in a real view are skipped here too: they
need the GUI.

selection.marks(context) -> {'pattern': set, 'placed': set, 'garment': set} is the place the marks are read from
once the selection is rebuilt. Until then the marks are read as the add-on draws them today.

Run: blender -b --factory-startup --python tests/test_selection_contract.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import (selection as SEL, toolbar as TB, pattern_view as PV, placed_view as PLV,  # noqa: E402
                           piece_move as PM, store as ST, dress as D)
from ac9_cloth_sim.core import spec as S, draft as DR        # noqa: E402

PLACED = ('A', 'B')       # adjusted shapes (before the garment, or Reset after it)
WORN = ('C', 'D')         # on the dressed garment
ALL = PLACED + WORN


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def marks(ctx):
    """The pieces drawn as picked, per view."""
    if hasattr(SEL, 'marks'):
        m = SEL.marks(ctx)
        return {k: set(v) for k, v in m.items()}
    picked = set(SEL.pieces(ctx))
    pattern = {ob[PV.PIECE_PROP] for ob in PV.piece_objects().values() if ob.select_get()}
    # overlay._draw_fills brightens the placed fills of SEL.pieces
    placed = picked & set(PLACED)
    # nothing tints the dressed garment's faces today (the handle is one arrow at the center)
    garment = set()
    return dict(pattern=pattern, placed=placed, garment=garment)


def promised(picked):
    """The promise (user 2026-11-14): every picked piece is marked in every view it shows in."""
    picked = set(picked)
    return dict(pattern=picked, placed=picked & set(PLACED), garment=picked & set(WORN))


class SelectionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = bpy.context
        spec = S.new_spec('SelectionContract')
        for i, n in enumerate(ALL):
            DR.add_polygon(spec, [[i * 100, 0], [i * 100 + 30, 0], [i * 100, 30]], name=n)
        ST.new_text(ctx, spec['name'])
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        spec, outlines = ST.get(ctx)
        PV.rebuild(ctx, spec, outlines)
        cls.placed = {}
        for i, n in enumerate(PLACED):
            me = bpy.data.meshes.new('SC_placed_' + n)
            me.from_pydata([(i, 0, 0), (i + .03, 0, 0), (i, 0, .03)], [], [(0, 1, 2)])
            ob = bpy.data.objects.new('SC_placed_' + n, me)
            PLV._collection(ctx).objects.link(ob)
            ob[PLV.PROP] = n
            ob[PLV.GARMENT_PROP] = spec['name']
            ob[PLV.ORG_PROP] = [0, 0, 0]
            cls.placed[n] = ob
        me = bpy.data.meshes.new('SC_garment')
        verts = []
        for i in range(len(WORN)):
            verts += [(i * .1, 1, 0), (i * .1 + .03, 1, 0), (i * .1, 1, .03)]
        me.from_pydata(verts, [], [(i * 3, i * 3 + 1, i * 3 + 2) for i in range(len(WORN))])
        g = bpy.data.objects.new(D.garment_name(spec), me)
        ctx.scene.collection.objects.link(g)
        g['ac9_piece_names'] = json.dumps(list(WORN))
        a = me.attributes.new(name='ac9_piece', type='INT', domain='FACE')
        a.data.foreach_set('value', list(range(len(WORN))))
        ctx.view_layer.update()
        cls.g = g

    def setUp(self):
        PM.clear(bpy.context)
        SEL.deselect_all(bpy.context)
        bpy.context.view_layer.update()

    # ---- the entries the tools call (toolbar.AC9_OT_SimToolClick, draft_tool pattern_drag)
    def pattern_click(self, n, shift=False):
        self.assertTrue(TB.select_piece(bpy.context, n, extend=shift))

    def placed_click(self, n, shift=False):
        self.assertTrue(TB.select_object(bpy.context, self.placed[n], extend=shift))

    def garment_click(self, n, shift=False):
        self.assertTrue(TB.pick_garment_piece(bpy.context, self.g, n, extend=shift))

    def assert_marks(self, picked):
        bpy.context.view_layer.update()
        self.assertEqual(marks(bpy.context), promised(picked))

    # ---- rows of docs/reviews/REVIEW_selection_matrix_2026-11-14.md
    def test_row01_pattern_shift_adds(self):
        self.pattern_click('A')
        self.pattern_click('C', shift=True)
        self.assertEqual(set(SEL.pieces(bpy.context)), {'A', 'C'})
        self.assertEqual(marks(bpy.context)['pattern'], {'A', 'C'})

    def test_row02_pattern_pick_marks_placed_shape(self):
        self.pattern_click('A')
        self.assertEqual(marks(bpy.context)['placed'], {'A'})

    def test_row03_pattern_pick_marks_garment(self):
        self.pattern_click('C')
        self.assert_marks({'C'})

    def test_row04_placed_pick_marks_pattern(self):
        self.placed_click('A')
        self.assert_marks({'A'})

    def test_row05_placed_shift_adds(self):
        self.placed_click('A')
        self.placed_click('B', shift=True)
        self.assertEqual(set(SEL.pieces(bpy.context)), {'A', 'B'})
        self.assertEqual(marks(bpy.context)['placed'], {'A', 'B'})

    @unittest.skip("needs Blender's own select operators in a real 3D view (GUI)")
    def test_row06_blender_select_in_3d(self):
        """Decided (user 2026-11-17): Blender's own click, box, lasso and circle do not pick pieces, they only drop
        them; other objects are picked as ever (selection._NATIVE). Checked in the GUI with simulated mouse events."""

    def test_row07_garment_shift_adds_and_removes(self):
        self.garment_click('C')
        self.garment_click('D', shift=True)
        self.assertEqual(set(SEL.pieces(bpy.context)), {'C', 'D'})
        self.garment_click('D', shift=True)
        self.assertEqual(set(SEL.pieces(bpy.context)), {'C'})

    def test_row08_garment_pick_marks_garment(self):
        self.garment_click('C')
        self.garment_click('D', shift=True)
        self.assertEqual(marks(bpy.context)['garment'], {'C', 'D'})

    def test_row09_garment_pick_marks_pattern(self):
        self.garment_click('C')
        self.garment_click('D', shift=True)
        self.assertEqual(marks(bpy.context)['pattern'], {'C', 'D'})

    def test_row10_pattern_replace_after_garment(self):
        self.garment_click('C')
        self.garment_click('D', shift=True)
        self.pattern_click('C')
        self.assertEqual(set(SEL.pieces(bpy.context)), {'C'})
        self.assert_marks({'C'})

    @unittest.skip("needs Blender's own select operators in a real 3D view (GUI)")
    def test_row11_select_box_on_garment(self):
        """Decided (b-5, user 2026-11-17): Blender's Select Box on the dressed garment picks the garment object, no
        piece; the Tool tab then shows one line and a button to Whole Pieces (tool_tab.draw_whole_garment_hint)."""

    def test_row12_placed_and_garment_mixed(self):
        """b-4 (user 2026-11-15): a placed piece and a garment piece picked together, each marked where it shows;
        Shift on either takes it out again."""
        self.placed_click('A')
        self.garment_click('C', shift=True)
        self.assertEqual(set(SEL.pieces(bpy.context)), {'A', 'C'})
        self.assert_marks({'A', 'C'})
        self.placed_click('B', shift=True)
        self.assert_marks({'A', 'B', 'C'})
        self.garment_click('C', shift=True)
        self.assert_marks({'A', 'B'})

    def test_row13_pattern_tools_see_3d_pick(self):
        self.placed_click('A')
        self.assertIn('A', SEL.pattern_pieces(bpy.context))
        PM.clear(bpy.context)
        self.garment_click('C')
        self.assertIn('C', SEL.pattern_pieces(bpy.context))

    def test_row14_tool_tab_lead(self):
        self.pattern_click('A')
        self.assertEqual(SEL.lead(bpy.context), 'A')
        self.placed_click('B')
        self.assertEqual(SEL.lead(bpy.context), 'B')
        self.garment_click('C')
        self.assertEqual(SEL.lead(bpy.context), 'C')

    def test_row15_last_shift_click_leads(self):
        """As Blender's own select: the piece Shift-clicked last is the active one (drawn in the active color, the
        Tool tab shows it), in every view and when views are mixed."""
        from ac9_cloth_sim import piece_look as PK
        for first, then, click in (('A', 'C', 'pattern'), ('A', 'B', 'placed'), ('C', 'D', 'garment'),
                                   ('A', 'C', 'mixed')):
            with self.subTest(click):
                self.setUp()
                if click == 'mixed':
                    self.placed_click(first)
                    self.garment_click(then, shift=True)
                else:
                    getattr(self, click + '_click')(first)
                    getattr(self, click + '_click')(then, shift=True)
                self.assertEqual(SEL.lead(bpy.context), then)
                PK._selected(bpy.context)
                self.assertEqual(PK._mark['lead'], then)


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SelectionContract))
    if out:
        rows = {}
        for t, _ in result.failures:
            rows[t.id().split('.')[-1]] = 'fail'
        for t, _ in result.errors:
            rows[t.id().split('.')[-1]] = 'error'
        for t, _ in result.skipped:
            rows[t.id().split('.')[-1]] = 'skip'
        for name in unittest.defaultTestLoader.getTestCaseNames(SelectionContract):
            rows.setdefault(name, 'pass')
        Path(out).write_text(json.dumps(dict(rows=dict(sorted(rows.items())),
                                             failures={t.id().split('.')[-1]: tb for t, tb in result.failures + result.errors}),
                                        ensure_ascii=False, indent=2), encoding='utf-8')
