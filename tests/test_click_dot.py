# SPDX-License-Identifier: GPL-3.0-or-later
"""A click on the flat pattern marks the same point on the cloth (garment_mark.mark_flat): the dressed garment's
triangle whose UVs (its layout points) hold the point, weighted as the point lies in it. A click on the outline (an
edge or a corner picked) lands a hair outside: the nearest triangle within EDGE_MM takes it. Off the piece: no dot.

Run: blender -b --factory-startup --python tests/test_click_dot.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import numpy as np                                           # noqa: E402

import ac9_cloth_sim                                         # noqa: E402
ac9_cloth_sim.register()
from ac9_cloth_sim import garment_mark as GM                 # noqa: E402


def garment():
    """Two pieces on one garment: A, a 400 x 600 mm sheet laid at layout (100..500, 0..600) mm, its cloth the same
    sheet doubled in size and stood up (x = 2u, z = 2v); B, a square elsewhere."""
    V = [(0.2, 0, 0), (1.0, 0, 0), (1.0, 0, 1.2), (0.2, 0, 1.2),
         (3, 0, 0), (3.2, 0, 0), (3.2, 0, 0.2), (3, 0, 0.2)]
    F = [(0, 1, 2, 3), (4, 5, 6, 7)]
    me = bpy.data.meshes.new("dot_test")
    me.from_pydata(V, [], F)
    uv = me.uv_layers.new(name="UVMap")
    UV = {0: (0.1, 0.0), 1: (0.5, 0.0), 2: (0.5, 0.6), 3: (0.1, 0.6),
          4: (1.0, 1.0), 5: (1.1, 1.0), 6: (1.1, 1.1), 7: (1.0, 1.1)}
    for lp in me.loops:
        uv.data[lp.index].uv = UV[lp.vertex_index]
    a = me.attributes.new('ac9_piece', 'INT', 'FACE')
    a.data[0].value, a.data[1].value = 0, 1
    ob = bpy.data.objects.new(GM.GARMENT_PREFIX + "dot_test", me)
    ob['ac9_piece_names'] = json.dumps(["A", "B"])
    bpy.context.scene.collection.objects.link(ob)
    return ob


class ClickDot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = garment()
        cls._shown = GM._shown
        GM._shown = lambda context, ob: True         # no window here: every object counts as shown

    @classmethod
    def tearDownClass(cls):
        GM._shown = cls._shown

    def cloth(self):
        return np.array(GM.points(bpy.context)[0])

    def test_inside_marks_the_same_point(self):
        for p in ((300.0, 300.0), (120.0, 50.0), (480.0, 590.0)):
            self.assertTrue(GM.mark_flat(bpy.context, "A", p), p)
            self.assertEqual(GM._mark['garment'], self.g.name)
            want = np.array([2 * p[0] / 1000.0, 0.0, 2 * p[1] / 1000.0])
            self.assertLess(np.linalg.norm(self.cloth() - want), 1e-5, p)

    def test_on_the_outline_takes_the_nearest(self):
        self.assertTrue(GM.mark_flat(bpy.context, "A", (500.0 + 1.0, 300.0)))      # 1 mm past the right edge
        self.assertLess(np.linalg.norm(self.cloth() - np.array([1.0, 0.0, 0.6])), 1e-5)

    def test_off_the_piece_no_dot(self):
        GM.mark_flat(bpy.context, "A", (300.0, 300.0))
        self.assertFalse(GM.mark_flat(bpy.context, "A", (700.0, 300.0)))          # 200 mm off: the old dot goes
        self.assertEqual(GM._mark, {})
        self.assertFalse(GM.mark_flat(bpy.context, "A", (1050.0, 1050.0)))        # inside B, not A
        self.assertFalse(GM.mark_flat(bpy.context, "Nope", (300.0, 300.0)))

    def test_the_other_piece(self):
        self.assertTrue(GM.mark_flat(bpy.context, "B", (1050.0, 1050.0)))
        self.assertEqual(GM._mark['piece'], "B")
        self.assertLess(np.linalg.norm(self.cloth() - np.array([3.1, 0.0, 0.1])), 1e-5)


class DeselectDot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = ClickDot.g
        cls.old_shown = GM._shown
        GM._shown = lambda context, ob: True

    @classmethod
    def tearDownClass(cls):
        GM._shown = cls.old_shown

    """Input-only synthetic sheets: positive-area finite geometry, no solver quality claim."""
    def setUp(self):
        self.assertTrue(all(p.area > 0 for p in self.g.data.polygons))
        self.assertTrue(all(np.isfinite(v.co[:]).all() for v in self.g.data.vertices))
        self.seed()
        from ac9_cloth_sim import draft_tool, toolbar, seam_edit_tool
        self.DT, self.TB, self.SE = draft_tool, toolbar, seam_edit_tool
        self.ctx = NS(scene=bpy.context.scene,screen=bpy.context.screen, area=NS(type='VIEW_3D', tag_redraw=lambda: None))

    def seed(self):
        self.assertTrue(GM.mark_flat(bpy.context, 'A', (300., 300.)))

    def test_whole_and_edit_blank_click(self):
        for mode in ('TRANSFORM', 'EDIT'):
            for bm in ('SET', 'ADD'):
                self.seed()
                op = NS(box=[(20,20),(20,20)], box_mode=bm, box_piece=None, _box_h=None, mode=mode)
                with patch.object(bpy.types.SpaceView3D, 'draw_handler_remove'), patch.object(self.DT.SEL, 'deselect_all'), patch.object(self.DT.SEL, '_drop_seam'):
                    r = self.DT.AC9_OT_SimPatternDrag._box_modal(op, self.ctx, NS(type='LEFTMOUSE', value='RELEASE'))
                self.assertEqual(r, {'CANCELLED'})
                self.assertEqual(bool(GM._mark), bm == 'ADD', (mode,bm))

    def test_place_blank_click(self):
        for kind in ('NONE','PLANE'):
            for shift in (False, True):
                self.seed()
                op = NS(p=dict(event=NS(shift=shift),kind=kind,idx=-1), _ob=lambda:None)
                with patch.object(self.TB.SEL,'deselect_all'), patch.object(self.TB.SEL,'_drop_seam'):
                    self.TB.AC9_OT_SimPlacePress._click(op,self.ctx)
                self.assertEqual(bool(GM._mark),shift,(kind,shift))

    def test_seam_blank_click(self):
        for kind in ('NONE','PLANE'):
            for shift in (False, True):
                self.seed()
                op = NS(_pick3d=lambda *args:False)
                ev = NS(mouse_region_x=20,mouse_region_y=20,shift=shift)
                with patch.object(self.SE.store,'get',return_value=({'pieces':[]},{})), patch.object(self.SE.PV,'layout_matrix',return_value=None), patch.object(self.TB,'under',return_value=(kind,None,-1)), patch.object(self.SE,'mouse_mm',return_value=None), patch.object(self.SE,'unselect_seam'):
                    self.SE.AC9_OT_SimSeamClick.invoke(op,self.ctx,ev)
                self.assertEqual(bool(GM._mark),shift,(kind,shift))

    def test_other_tool_blank_even_when_hint_rejects(self):
        op = NS(tool='TAPE_ALONG')
        ev = NS(mouse_region_x=20,mouse_region_y=20,shift=False,value='PRESS')
        with patch.object(self.TB,'under',return_value=('NONE',None,-1)), patch.object(self.TB,'hint',return_value=False):
            self.TB.AC9_OT_SimToolClick.invoke(op,self.ctx,ev)
        self.assertFalse(GM._mark)

    def test_box_drag_does_not_clear(self):
        op = NS(box=[(20,20),(120,120)],box_mode='SET',box_piece=None,_box_h=None,mode='EDIT')
        with patch.object(bpy.types.SpaceView3D,'draw_handler_remove'), patch.object(self.DT,'box_points'):
            self.DT.AC9_OT_SimPatternDrag._box_modal(op,self.ctx,NS(type='LEFTMOUSE',value='RELEASE'))
        self.assertTrue(GM._mark)

    def test_clear_redraws_all_views(self):
        a,b = Mock(type='VIEW_3D'),Mock(type='VIEW_3D')
        GM.clear(NS(screen=NS(areas=[a,b])))
        self.assertIsNone(GM.points(bpy.context))
        a.tag_redraw.assert_called_once()
        b.tag_redraw.assert_called_once()


if __name__ == '__main__':
    r = unittest.main(argv=['x'], exit=False, verbosity=2).result
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps({'ran': r.testsRun, 'failures': len(r.failures),
                                             'errors': len(r.errors)}), encoding='utf-8')
    import os
    os._exit(0 if r.wasSuccessful() else 1)
