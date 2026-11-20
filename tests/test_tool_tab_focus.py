# SPDX-License-Identifier: GPL-3.0-or-later
"""The Tool tab after a click on a piece already selected: a fabric or an interlining picked in Lists > Fabrics in
between stayed in the tab, as the click changed no selection (the tab follows the selection's changes).

Each click entry the tools call (the pattern click, the placed click, the garment click): pick the piece, show a
fabric / an interlining in the tab, click the same piece again -> the tab shows AC9 Piece.

Run: blender -b --factory-startup --python tests/test_tool_tab_focus.py -- <parent of ac9_cloth_sim> [out.json]
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
                           piece_move as PM, store as ST, dress as D, tool_tab as TT)
from ac9_cloth_sim.core import spec as S, draft as DR, fabric as FA  # noqa: E402

PLACED = ('A',)
WORN = ('C',)
ALL = ('A', 'B', 'C')


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class ToolTabFocus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = bpy.context
        spec = S.new_spec('ToolTabFocus')
        for i, n in enumerate(ALL):
            DR.add_polygon(spec, [[i * 100, 0], [i * 100 + 30, 0], [i * 100, 30]], name=n)
        cls.fid = FA.add_custom(spec, 'Test Fabric')
        ST.new_text(ctx, spec['name'])
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        spec, outlines = ST.get(ctx)
        PV.rebuild(ctx, spec, outlines)
        cls.placed = {}
        for i, n in enumerate(PLACED):
            me = bpy.data.meshes.new('TT_placed_' + n)
            me.from_pydata([(i, 0, 0), (i + .03, 0, 0), (i, 0, .03)], [], [(0, 1, 2)])
            ob = bpy.data.objects.new('TT_placed_' + n, me)
            PLV._collection(ctx).objects.link(ob)
            ob[PLV.PROP] = n
            ob[PLV.GARMENT_PROP] = spec['name']
            ob[PLV.ORG_PROP] = [0, 0, 0]
            cls.placed[n] = ob
        me = bpy.data.meshes.new('TT_garment')
        me.from_pydata([(0, 1, 0), (.03, 1, 0), (0, 1, .03)], [], [(0, 1, 2)])
        g = bpy.data.objects.new(D.garment_name(spec), me)
        ctx.scene.collection.objects.link(g)
        g['ac9_piece_names'] = json.dumps(list(WORN))
        a = me.attributes.new(name='ac9_piece', type='INT', domain='FACE')
        a.data.foreach_set('value', [0])
        ctx.view_layer.update()
        cls.g = g

    def setUp(self):
        PM.clear(bpy.context)
        SEL.deselect_all(bpy.context)
        bpy.context.view_layer.update()

    def shown(self):
        bpy.context.view_layer.update()
        return TT.resolve(bpy.context, ST.get(bpy.context)[0])

    def round_trip(self, click, kind):
        click()
        self.assertEqual(self.shown(), 'PIECE')
        # a row of Lists > Fabrics: the tab shows the fabric / interlining, the piece stays selected
        TT.focus_item(bpy.context, kind, self.fid if kind == 'FABRIC' else self.bond)
        self.assertEqual(self.shown(), kind)
        click()                                         # the same piece again: no selection change
        self.assertEqual(self.shown(), 'PIECE')

    def test_pattern_click_after_fabric(self):
        self.round_trip(lambda: self.assertTrue(TB.select_piece(bpy.context, 'B')), 'FABRIC')

    def test_placed_click_after_fabric(self):
        self.round_trip(lambda: self.assertTrue(TB.select_object(bpy.context, self.placed['A'])), 'FABRIC')

    def test_garment_click_after_fabric(self):
        self.round_trip(lambda: self.assertTrue(TB.pick_garment_piece(bpy.context, self.g, 'C')), 'FABRIC')


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[str(t[0]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-400:]) for t in res.errors])), 'utf-8')
