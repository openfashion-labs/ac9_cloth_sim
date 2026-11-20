# SPDX-License-Identifier: GPL-3.0-or-later
"""The Place tool's press (toolbar.AC9_OT_SimPlacePress) and the 3D box select of pieces (box_pieces, Whole Pieces).

A click only selects: on a placed piece or a dressed piece it picks that piece (Shift adds), on nothing it drops the
selection (Shift: keeps it). It never places, and leaves no undo step (it ends CANCELLED, what it picked kept). A drag from the dressed garment does not move it (it is a surface to
place on); pieces being worn are left out of a placing drag (split_worn). A placed piece slides on its guide wherever it
is held (body_axes.ray_nearest). The box picks placed pieces and the dressed garment's pieces one by one (SET / ADD /
SUB), and a box over nothing picks nothing.

The modal parts (the move past the drag threshold, the release) need a window; here the press's click and drag
decisions are called directly, as its modal calls them.

Run: blender -b --factory-startup --python tests/test_place_click_drag.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import types
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
from mathutils import Matrix                                 # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import (selection as SEL, toolbar as TB, pattern_view as PV, placed_view as PLV,  # noqa: E402
                           piece_move as PM, store as ST, dress as D, box_pieces as BX,
                           placement as PL)
from ac9_cloth_sim.core import spec as S, draft as DR, body_axes as BA  # noqa: E402
import numpy as np                                           # noqa: E402

PLACED = ('A',)
WORN = ('C', 'E')
ALL = ('A', 'B', 'C', 'E')
SIZE = 1000                     # region pixels; the view looks along +Y, 1 m across: x -> px, z -> py


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class _View:
    """bpy.context with a front orthographic 3D view (region, region_data) and no space (every object allowed)."""
    region = types.SimpleNamespace(width=SIZE, height=SIZE, type='WINDOW')
    # x in [-0.5, 0.5] m and z in [-0.5, 0.5] m fill the region
    region_data = types.SimpleNamespace(perspective_matrix=Matrix(((2, 0, 0, 0), (0, 0, 2, 0), (0, -1, 0, 0),
                                                                  (0, 0, 0, 1))))
    space_data = None

    def __getattr__(self, name):
        return getattr(bpy.context, name)


def px(x, z):
    return ((x * 2) * 0.5 + 0.5) * SIZE, ((z * 2) * 0.5 + 0.5) * SIZE


def box(x0, z0, x1, z1):
    (a, b), (c, d) = px(x0, z0), px(x1, z1)
    return (min(a, c), min(b, d), max(a, c), max(b, d))


class PlaceClickDrag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = bpy.context
        spec = S.new_spec('PlaceClickDrag')
        for i, n in enumerate(ALL):
            DR.add_polygon(spec, [[i * 100, 0], [i * 100 + 30, 0], [i * 100, 30]], name=n)
        ST.new_text(ctx, spec['name'])
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        spec, outlines = ST.get(ctx)
        PV.rebuild(ctx, spec, outlines)
        # the flat pattern sits far off the 3D view's boxes (it is not what these tests box)
        for ob in PV.piece_objects().values():
            ob.location.z += 5.0
        me = bpy.data.meshes.new('PCD_placed_A')
        me.from_pydata([(-0.40, 0, 0.0), (-0.35, 0, 0.0), (-0.40, 0, 0.05)], [], [(0, 1, 2)])
        ob = bpy.data.objects.new('PCD_placed_A', me)
        PLV._collection(ctx).objects.link(ob)
        ob[PLV.PROP] = 'A'
        ob[PLV.GARMENT_PROP] = spec['name']
        ob[PLV.ORG_PROP] = [0, 0, 0]
        cls.placed = ob
        # the dressed garment: piece C round x = 0.0, piece E round x = 0.3
        me = bpy.data.meshes.new('PCD_garment')
        me.from_pydata([(0.0, 0.2, 0.0), (0.05, 0.2, 0.0), (0.0, 0.2, 0.05),
                        (0.3, 0.2, 0.0), (0.35, 0.2, 0.0), (0.3, 0.2, 0.05)], [], [(0, 1, 2), (3, 4, 5)])
        g = bpy.data.objects.new(D.garment_name(spec), me)
        ctx.scene.collection.objects.link(g)
        g['ac9_piece_names'] = json.dumps(list(WORN))
        g[D.JOB_MISSING_PROP] = True         # dressed (its result is its own mesh): its pieces are worn
        a = me.attributes.new(name='ac9_piece', type='INT', domain='FACE')
        a.data.foreach_set('value', [0, 1])
        cls.g = g
        cls.other = bpy.data.objects.new('PCD_other', bpy.data.meshes.new('PCD_other'))
        ctx.scene.collection.objects.link(cls.other)
        ctx.view_layer.update()

    def setUp(self):
        PM.clear(bpy.context)
        SEL.deselect_all(bpy.context)
        bpy.context.view_layer.update()

    def picked(self):
        bpy.context.view_layer.update()
        return sorted(SEL.ids(bpy.context))

    def press(self, kind, ob=None, idx=0, shift=False):
        """The press as the Place tool leaves it for its operator."""
        ev = types.SimpleNamespace(mouse_region_x=10, mouse_region_y=10, mouse_x=10, mouse_y=10, shift=shift,
                                   ctrl=False, alt=False, oskey=False, type='LEFTMOUSE', value='PRESS')
        op = types.SimpleNamespace(p=dict(kind=kind, ob=ob.name if ob is not None else None, idx=idx, event=ev))
        op._ob = lambda: bpy.data.objects.get(op.p['ob']) if op.p['ob'] else None
        return op

    def click(self, *a, **k):
        op = self.press(*a, **k)
        return TB.AC9_OT_SimPlacePress._click(op, bpy.context)

    # ---- a click selects, never places

    def test_click_on_nothing_drops_the_selection(self):
        TB.select_piece(bpy.context, 'B')
        TB.select_object(bpy.context, self.placed, extend=True)
        self.other.select_set(True)
        self.assertEqual(self.picked(), ['A', 'B'])
        for kind in ('NONE', 'BODY', 'OTHER', 'PLANE'):
            TB.select_piece(bpy.context, 'B')
            spec_before = ST.text(bpy.context).as_string()
            self.assertEqual(self.click(kind, self.other if kind == 'OTHER' else None), {'CANCELLED'})
            self.assertEqual(self.picked(), [], kind)
            self.assertFalse(self.other.select_get(), kind)
            self.assertEqual(ST.text(bpy.context).as_string(), spec_before, kind)   # nothing placed

    def test_shift_click_on_nothing_keeps_it(self):
        TB.select_piece(bpy.context, 'B')
        self.assertEqual(self.click('NONE', shift=True), {'CANCELLED'})
        self.assertEqual(self.picked(), ['B'])

    def test_click_on_a_placed_piece_picks_it(self):
        TB.select_piece(bpy.context, 'B')
        self.assertEqual(self.click('PLACED', self.placed), {'CANCELLED'})
        self.assertEqual(self.picked(), ['A'])
        self.assertEqual(self.click('PLACED', self.placed, shift=True), {'CANCELLED'})   # Shift: taken out
        self.assertEqual(self.picked(), [])

    def test_click_on_the_garment_picks_its_piece(self):
        TB.select_piece(bpy.context, 'B')                 # pieces to place selected: still only a pick
        self.assertEqual(self.click('GARMENT', self.g, idx=1), {'CANCELLED'})
        self.assertEqual(self.picked(), ['E'])
        self.assertEqual(self.click('GARMENT', self.g, idx=0, shift=True), {'CANCELLED'})
        self.assertEqual(self.picked(), ['C', 'E'])

    def test_drag_from_nothing_with_nothing_selected_places_nothing(self):
        op = self.press('NONE')
        spec_before = ST.text(bpy.context).as_string()
        self.assertEqual(TB.AC9_OT_SimPlacePress._drag_(op, bpy.context, op.p['event']), {'CANCELLED'})
        self.assertEqual(ST.text(bpy.context).as_string(), spec_before)

    def test_drag_from_the_garment_does_not_move_it(self):
        TB.pick_garment_piece(bpy.context, self.g, 'C')
        m0 = self.g.matrix_world.copy()
        op = self.press('GARMENT', self.g, idx=0)
        op._drag_ = lambda c, e: TB.AC9_OT_SimPlacePress._drag_(op, c, e)
        op._worn_note = lambda: None
        # in the background Place's poll fails (no 3D view): the drag goes to Place, which the wrapper reports
        self.assertEqual(TB.AC9_OT_SimPlacePress._drag(op, bpy.context, op.p['event']), {'CANCELLED'})
        self.assertFalse(getattr(op, 'movers', None))
        self.assertEqual(self.g.matrix_world, m0)

    def test_worn_pieces_are_left_out(self):
        spec = ST.get(bpy.context)[0]
        self.assertEqual(PL.split_worn(spec, ['A', 'B', 'C', 'E']), (['A', 'B'], ['C', 'E']))
        self.assertEqual(PL.split_worn(spec, ['C']), ([], ['C']))
        D.set_shown_as_placed(self.g, spec, ['C'])           # C put back to placed: placed again by a drag
        try:
            self.assertEqual(PL.split_worn(spec, ['C', 'E']), (['C'], ['E']))
        finally:
            D.set_shown_as_placed(self.g, spec, [])

    def test_right_click_rows(self):
        # a placed piece and a dressed piece: each its own rows; both can be moved, turned, set a state and hidden
        from ac9_cloth_sim import draft_tool as DTL
        spec = ST.get(bpy.context)[0]
        labels = {k: [r[0] for r in DTL.menu_items(spec, (k, 'A')) if r is not None] for k in ('placed', 'garment')}
        for k in ('placed', 'garment'):
            for want in ("Move", "Rotate", "Back to Guide", "Back to Flat", "Mirror Placement", "Swap Sides", "State",
                         "Hide"):
                self.assertIn(want, labels[k], k)
        self.assertNotIn("Back to Placed", labels['placed'])
        self.assertIn("Back to Placed", labels['garment'])

    # ---- sliding on the guide wherever the piece is held

    def test_ray_nearest_slides_beside_the_cylinder(self):
        g = {'o': [0, 0, 0], 'R': np.eye(3).tolist(), 'r': 100.0, 'h0': 0.0, 'h1': 500.0}
        d = [0, 1, 0]                                     # looking along +y
        # beside the cylinder (x = 150 > r): the spot on the side wall nearest the ray, at the ray's height
        th, h, _ = BA.ray_nearest(g, [150, -1000, 200], d)
        self.assertAlmostEqual(th, 0.0, places=6)
        self.assertAlmostEqual(h, 200.0, places=6)
        th, _, _ = BA.ray_nearest(g, [-150, -1000, 200], d)
        self.assertAlmostEqual(abs(th), 180.0, places=6)
        # past the end: held at the end
        self.assertAlmostEqual(BA.ray_nearest(g, [150, -1000, 900], d)[1], 500.0)
        # grazing the wall: the same spot as the hit, so the slide does not jump where it leaves the cylinder
        hit = BA.ray_hit({'t': g}, [99.999, -1000, 200], d)
        near = BA.ray_nearest(g, [100.001, -1000, 200], d)
        self.assertLess(abs(hit[1] - near[0]), 1.0)
        self.assertAlmostEqual(hit[2], near[1], places=6)
        # along the axis: nothing
        self.assertIsNone(BA.ray_nearest(g, [0, 0, -1000], [0, 0, 1]))

    # ---- the box: pieces one by one, also on the dressed garment

    def test_box_picks_garment_pieces_one_by_one(self):
        v = _View()
        self.assertEqual(BX.world_names(v, box(-0.05, -0.05, 0.1, 0.1)), ['C'])
        self.assertEqual(BX.world_names(v, box(0.25, -0.05, 0.4, 0.1)), ['E'])
        self.assertEqual(BX.world_names(v, box(-0.45, -0.05, 0.4, 0.1)), ['A', 'C', 'E'])
        self.assertEqual(BX.world_names(v, box(0.1, -0.05, 0.2, 0.1)), [])          # between them: nothing

    def test_box_select_modes(self):
        v = _View()
        BX.select_box(v, box(-0.05, -0.05, 0.1, 0.1), 'SET')
        self.assertEqual(self.picked(), ['C'])
        BX.select_box(v, box(-0.45, -0.05, -0.3, 0.1), 'ADD')
        self.assertEqual(self.picked(), ['A', 'C'])
        BX.select_box(v, box(-0.05, -0.05, 0.1, 0.1), 'SUB')
        self.assertEqual(self.picked(), ['A'])
        BX.select_box(v, box(0.25, -0.05, 0.4, 0.1), 'SET')
        self.assertEqual(self.picked(), ['E'])

    def test_hidden_garment_is_not_boxed(self):
        v = _View()
        self.g.hide_set(True)
        try:
            self.assertEqual(BX.world_names(v, box(-0.45, -0.05, 0.4, 0.1)), ['A'])
        finally:
            self.g.hide_set(False)


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[str(t[0]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-400:]) for t in res.errors])), 'utf-8')
