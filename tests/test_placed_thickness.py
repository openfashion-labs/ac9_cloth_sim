# SPDX-License-Identifier: GPL-3.0-or-later
"""The placed pieces drawn with their fabric's thickness (placed_view.refresh_thickness).

The same switch and side as the dressed garment's (Overlays > Thickness): a Solidify after the pair's Mirror, one value
per object (the piece's fabric), none with the switch off or the piece set to no thickness. Display only: the readers
of a placed object's evaluated mesh (placed_face_piece, _read, box_pieces.placed_names) and its own shape (hand_shape)
read the same with and without it.

Run: blender -b --factory-startup --python tests/test_placed_thickness.py -- <parent of ac9_cloth_sim> [out.json]
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
import numpy as np                                           # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import (placed_view as PLV, store as ST, pattern_view as PV, box_pieces as BX,  # noqa: E402
                           display_state as DS)
from ac9_cloth_sim.core import spec as S, draft as DR, fabric as FA  # noqa: E402

SIZE = 1000


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class _View:
    """bpy.context with a front orthographic 3D view (x, z in [-0.5, 0.5] m fill the region), every object allowed."""
    region = types.SimpleNamespace(width=SIZE, height=SIZE, type='WINDOW')
    region_data = types.SimpleNamespace(perspective_matrix=Matrix(((2, 0, 0, 0), (0, 0, 2, 0), (0, -1, 0, 0),
                                                                  (0, 0, 0, 1))))
    space_data = None

    def __getattr__(self, name):
        return getattr(bpy.context, name)


def _grid(nx=6, ny=4, x0=0.05, z0=0.0, w=0.12, h=0.08):
    """A small flat strip in the x-z plane (y = 0), quads split in two."""
    V = [(x0 + w * i / nx, 0.0, z0 + h * j / ny) for j in range(ny + 1) for i in range(nx + 1)]
    T = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            T += [(a, a + 1, a + nx + 2), (a, a + nx + 2, a + nx + 1)]
    return V, T


def _evaluated(ob):
    bpy.context.view_layer.update()
    me = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get('co', co)
    return co.reshape(-1, 3), len(me.polygons)


class PlacedThickness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = bpy.context
        spec = S.new_spec('PlacedThickness')
        for i, n in enumerate(('A', 'B', 'C')):
            DR.add_polygon(spec, [[i * 100, 0], [i * 100 + 30, 0], [i * 100, 30]], name=n)
        ST.new_text(ctx, spec['name'])
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        spec, outlines = ST.get(ctx)
        PV.rebuild(ctx, spec, outlines)
        for ob in PV.piece_objects().values():
            ob.location.z += 5.0                 # the flat pattern away from the boxes below
        cls.mm = FA.thickness(FA.resolved(spec, 'A'))
        V, T = _grid()
        cls.nv = len(V)
        # A: one piece, its pair B made by the Mirror (x = 0)
        cls.pair = cls._make('PT_placed_A', V, T, 'A', spec)
        cen = bpy.data.objects.new('PT_center', None)
        ctx.scene.collection.objects.link(cen)
        PLV._add_mirror(cls.pair, cen, 'B')
        # C: a single piece, off to the side
        V2 = [(x, 0.0, z - 0.3) for x, _, z in V]
        cls.single = cls._make('PT_placed_C', V2, T, 'C', spec)
        ctx.scene.ac9_cloth_sim.thickness_side = 'INSIDE'
        ctx.scene.ac9_cloth_sim.show_thickness = False       # the shapes without it (the switch's update removes it)
        ctx.view_layer.update()
        cls.before = {ob.name: _evaluated(ob) for ob in (cls.pair, cls.single)}
        ctx.scene.ac9_cloth_sim.show_thickness = True

    @classmethod
    def _make(cls, name, V, T, piece, spec):
        me = bpy.data.meshes.new(name)
        me.from_pydata(V, [], T)
        a = me.attributes.new(PLV.FLAT_ATTR, 'FLOAT2', 'POINT')
        a.data.foreach_set('vector', np.asarray([(x * 1000, z * 1000) for x, _, z in V], np.float32).ravel())
        ob = bpy.data.objects.new(name, me)
        PLV._collection(bpy.context).objects.link(ob)
        ob[PLV.PROP] = piece
        ob[PLV.GARMENT_PROP] = spec['name']
        ob[PLV.ORG_PROP] = [0, 0, 0]
        return ob

    def setUp(self):
        self.ctx = bpy.context
        self.ctx.scene.ac9_cloth_sim.show_thickness = True
        self.ctx.scene.ac9_cloth_sim.thickness_side = 'INSIDE'
        self.spec = ST.get(self.ctx)[0]
        PLV.refresh_thickness(self.ctx, self.spec)
        self.ctx.view_layer.update()

    def test_fabric_has_a_thickness(self):
        self.assertTrue(self.mm and self.mm > 0, "the default fabric is drawn with a thickness")

    def test_solidify_after_the_mirror(self):
        names = [m.name for m in self.pair.modifiers]
        self.assertEqual(names, ["AC9 Pair", PLV.THICK_MOD])
        m = self.pair.modifiers[PLV.THICK_MOD]
        self.assertAlmostEqual(m.thickness, self.mm / 1000.0, places=9)
        self.assertEqual(m.offset, -1.0)
        self.assertFalse(m.show_in_editmode)
        self.assertEqual([m.name for m in self.single.modifiers], [PLV.THICK_MOD])

    def test_mirror_added_later_stays_before(self):
        ob = self.single
        cen = bpy.data.objects['PT_center']
        PLV._add_mirror(ob, cen, 'B')
        try:
            self.assertEqual([m.name for m in ob.modifiers], ["AC9 Pair", PLV.THICK_MOD])
        finally:
            ob.modifiers.remove(ob.modifiers["AC9 Pair"])
            ob.pop(PLV.TWIN_PROP, None)

    def test_switch_side_and_piece_off(self):
        props = self.ctx.scene.ac9_cloth_sim
        props.thickness_side = 'OUTSIDE'
        self.assertEqual(self.single.modifiers[PLV.THICK_MOD].offset, 1.0)
        props.show_thickness = False                         # the update callback refreshes the adjusted shapes
        self.assertIsNone(self.single.modifiers.get(PLV.THICK_MOD))
        self.assertIsNone(self.pair.modifiers.get(PLV.THICK_MOD))
        props.show_thickness = True
        self.assertIsNotNone(self.single.modifiers.get(PLV.THICK_MOD))
        sp = S.loads(S.dumps(self.spec))
        for pc in sp['pieces']:
            if pc['name'] == 'C':
                pc['thickness'] = False
        PLV.refresh_thickness(self.ctx, sp)
        self.assertIsNone(self.single.modifiers.get(PLV.THICK_MOD))
        self.assertIsNotNone(self.pair.modifiers.get(PLV.THICK_MOD))

    def test_display_state_apply_draws_it(self):
        self.single.modifiers.remove(self.single.modifiers[PLV.THICK_MOD])
        DS.apply(self.ctx, self.spec)
        self.assertIsNotNone(self.single.modifiers.get(PLV.THICK_MOD))

    def test_copies_appended_in_order_the_shape_unmoved(self):
        # what core_count relies on: the Solidify doubles the vertices, the first half where they were
        for ob in (self.pair, self.single):
            (W0, F0), (W1, F1) = self.before[ob.name], _evaluated(ob)
            self.assertEqual(len(W1), 2 * len(W0), ob.name)
            self.assertEqual(PLV.core_count(ob, len(W1)), len(W0), ob.name)
            self.assertLess(np.abs(W1[:len(W0)] - W0).max(), 1e-7, ob.name)
            d = np.linalg.norm(W1[len(W0):] - W0, axis=1)       # each copy its vertex's, a thickness away
            self.assertLess(np.abs(d - self.mm / 1000.0).max(), 1e-7, ob.name)
            self.assertGreater(F1, 2 * F0, ob.name)             # the copy's faces and the rim

    def test_face_halves(self):
        ob = self.pair
        _W, nf = _evaluated(ob)
        own, n0 = len(ob.data.polygons), self.nv
        me = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        for fi in range(nf):
            vs = [v % (2 * n0) for v in me.polygons[fi].vertices]
            want = 'A' if max(vs) < n0 else 'B' if min(vs) >= n0 else None
            self.assertEqual(PLV.placed_face_piece(self.ctx, ob, fi), want, fi)
        self.assertEqual(PLV.placed_face_piece(self.ctx, ob, 0), 'A')
        self.assertEqual(PLV.placed_face_piece(self.ctx, ob, own), 'B')
        self.assertEqual(PLV.placed_face_piece(self.ctx, ob, 2 * own), 'A')       # the copy of a face of A

    def test_read_same_with_and_without(self):
        dg = self.ctx.evaluated_depsgraph_get()
        PLV._ev.clear()
        with_t = {ob.name: PLV._read(self.ctx, ob, dg) for ob in (self.pair, self.single)}
        with_t = {k: (v['n'], v['F'].copy(), v['T'].copy(), v['W'][:v['n']].copy()) for k, v in with_t.items()}
        self.ctx.scene.ac9_cloth_sim.show_thickness = False
        self.ctx.view_layer.update()
        dg = self.ctx.evaluated_depsgraph_get()
        PLV._ev.clear()
        for ob in (self.pair, self.single):
            c = PLV._read(self.ctx, ob, dg)
            n, F, T, W = with_t[ob.name]
            self.assertEqual(c['n'], n, ob.name)
            self.assertEqual(n, self.nv, ob.name)
            self.assertTrue(np.array_equal(c['F'], F), ob.name)
            self.assertTrue(np.array_equal(c['T'], T), ob.name)
            self.assertLess(np.abs(c['W'][:n] - W).max(), 1e-9, ob.name)

    def test_hand_shape_is_the_mesh(self):
        F, T, W = PLV.hand_shape(self.single)
        self.assertEqual(len(W), self.nv)
        self.assertEqual(len(T), len(self.single.data.polygons))

    def test_box_picks_the_same(self):
        v = _View()
        rect_a = (int(0.5 * SIZE + 0.04 * SIZE), int(0.5 * SIZE - 0.01 * SIZE), SIZE, int(0.6 * SIZE))  # A only
        rect_b = (0, int(0.5 * SIZE - 0.01 * SIZE), int(0.5 * SIZE - 0.04 * SIZE), SIZE)    # x < -0.04, z > 0: B
        rect_c = (int(0.5 * SIZE + 0.04 * SIZE), 0, SIZE, int(0.5 * SIZE - 0.2 * SIZE))     # z < -0.2: C
        got = [sorted(BX.placed_names(v, r)) for r in (rect_a, rect_b, rect_c)]
        self.ctx.scene.ac9_cloth_sim.show_thickness = False
        self.ctx.view_layer.update()
        got_off = [sorted(BX.placed_names(v, r)) for r in (rect_a, rect_b, rect_c)]
        self.assertEqual(got, got_off)
        self.assertEqual(got, [['A'], ['B'], ['C']])

    def test_click_dot_from_the_back(self):
        # a click on a placed piece seen from its back lands on the thickness's copy: the same pattern point as from
        # the front (it gave none: the copy's vertices read as outside the shape)
        from mathutils import Vector
        from ac9_cloth_sim import garment_mark as GM
        for ob, x, z, piece in ((self.single, 0.11, -0.26, 'C'), (self.pair, 0.11, 0.04, 'A'),
                                (self.pair, -0.11, 0.04, 'B')):
            got = {}
            for side, s in (('front', -1.0), ('back', 1.0)):       # the strip faces -y; inside = +y
                got[side] = GM.mark_ray(self.ctx, ob, Vector((x, 0.01 * s, z)), Vector((0.0, -s, 0.0)))
                self.assertIsNotNone(got[side], (piece, side))
                self.assertEqual(GM._mark['piece'], piece, (piece, side))
            self.assertLess(np.linalg.norm(np.subtract(got['front'], got['back'])), 0.01, piece)
            if piece == 'C':
                self.assertLess(np.linalg.norm(np.subtract(got['front'], (110.0, -260.0))), 0.01)
        GM.clear()

    def test_dot_state_back_and_hidden(self):
        # the dot seen from each side (user 2026-11-20): the strip faces -y, a view from -y sees it, from +y its back
        from mathutils import Vector
        from ac9_cloth_sim import garment_mark as GM
        shown, GM._shown = GM._shown, (lambda context, ob: True)
        for ob in [o for o in bpy.data.objects if o.name == 'Cube']:    # the factory scene's cube holds the rays
            bpy.data.objects.remove(ob)
        self.ctx.view_layer.update()
        front =(Vector((0.11, -1.0, -0.26)), Vector((0, 1, 0)))
        back = (Vector((0.11, 1.0, -0.26)), Vector((0, -1, 0)))
        try:
            self.assertTrue(GM.mark_flat(self.ctx, 'C', (110.0, -260.0)))
            self.assertEqual(GM.cloth_state(self.ctx, *front), GM.VISIBLE)
            self.assertEqual(GM.cloth_state(self.ctx, *back), GM.BACK)        # its own thickness does not hide it
            # clicked from the back: the dot on the thickness's face, which looks at that view
            self.assertIsNotNone(GM.mark_ray(self.ctx, self.single, *back))
            self.assertEqual(GM.cloth_state(self.ctx, *back), GM.VISIBLE)
            self.assertEqual(GM.cloth_state(self.ctx, *front), GM.BACK)
            # something in front: hidden; the same as a wireframe: not
            GM.mark_flat(self.ctx, 'C', (110.0, -260.0))
            me = bpy.data.meshes.new('PT_block')
            me.from_pydata([(0.0, -0.05, -0.35), (0.2, -0.05, -0.35), (0.2, -0.05, -0.2), (0.0, -0.05, -0.2)], [],
                           [(0, 1, 2, 3)])
            block = bpy.data.objects.new('PT_block', me)
            self.ctx.scene.collection.objects.link(block)
            self.ctx.view_layer.update()
            try:
                self.assertEqual(GM.cloth_state(self.ctx, *front), GM.HIDDEN)
                self.assertEqual(GM.cloth_state(self.ctx, *back), GM.BACK)
                block.display_type = 'WIRE'
                self.assertEqual(GM.cloth_state(self.ctx, *front), GM.VISIBLE)
            finally:
                bpy.data.objects.remove(block)
                bpy.data.meshes.remove(me)
            # the Mirror's half marked from the pattern: its normal turned back (it would read as its back)
            for piece, x in (('A', 0.11), ('B', -0.11)):
                q = GM.mark_ray(self.ctx, self.pair, Vector((x, -1.0, 0.04)), Vector((0, 1, 0)))   # its pattern point
                self.assertEqual(GM._mark['piece'], piece)
                if not GM.mark_flat(self.ctx, piece, q):
                    self.fail(piece)
                p = np.array(GM.points(self.ctx)[0])
                self.assertLess(abs(p[0] - x), 1e-4, piece)
                o = Vector((x, -1.0, 0.04))
                self.assertEqual(GM.cloth_state(self.ctx, o, Vector((0, 1, 0))), GM.VISIBLE, piece)
                self.assertEqual(GM.cloth_state(self.ctx, Vector((x, 1.0, 0.04)), Vector((0, -1, 0))), GM.BACK, piece)
        finally:
            GM._shown = shown
            GM.clear()

    def test_dot_hidden_by_own_layer(self):
        # two layers of one cloth 2 mm apart (a lapel over the front): a dot on the back layer is hidden by the
        # front one, whatever the gap; a dot on the front layer is seen (user 2026-11-20)
        from mathutils import Vector
        from ac9_cloth_sim import garment_mark as GM
        shown, GM._shown = GM._shown, (lambda context, ob: True)
        for ob in [o for o in bpy.data.objects if o.name == 'Cube']:
            bpy.data.objects.remove(ob)
        V = [(x, y, z) for y in (-0.402, -0.4) for x, z in ((0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1))]
        me = bpy.data.meshes.new('PT_layers')
        me.from_pydata(V, [], [(0, 1, 2, 3), (4, 5, 6, 7)])            # both face -y
        lay = bpy.data.objects.new('PT_layers', me)
        self.ctx.scene.collection.objects.link(lay)
        self.ctx.view_layer.update()
        o, d = Vector((0.05, -1.0, 0.05)), Vector((0, 1, 0))
        try:
            for verts, want in (((4, 5, 6), GM.HIDDEN), ((0, 1, 2), GM.VISIBLE)):
                GM._mark.clear()
                GM._mark.update(garment=lay.name, piece=None, verts=verts, w=(0.5, 0.25, 0.25), uv=(0, 0), nsign=1.0)
                self.assertEqual(GM.cloth_state(self.ctx, o, d), want, verts)
        finally:
            GM._shown = shown
            GM.clear()
            bpy.data.objects.remove(lay)
            bpy.data.meshes.remove(me)


if __name__ == '__main__':
    r = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps({'ran': r.testsRun, 'failures': len(r.failures), 'errors': len(r.errors),
                                             'detail': [str(t) + '\n' + s for t, s in r.failures + r.errors]},
                                            indent=1), encoding='utf-8')
    sys.exit(0 if r.wasSuccessful() else 1)
