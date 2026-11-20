# SPDX-License-Identifier: GPL-3.0-or-later
"""The object fields picked with the eyedropper (or by name): Lists > Collision's Add registers the object and empties
itself, the Fasteners' own-button field makes the mesh a style and empties itself, From Mesh works on its Mesh field
(else the active object), and none of them takes a pattern piece or a placed piece. A registered collision object is
what Whole Pieces picks in the 3D view ('OBSTACLE').

Run: blender -b --factory-startup --python tests/test_eyedropper_fields.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import (store as ST, dress as D, pattern_view as PV, placed_view as PLV,  # noqa: E402
                           mesh_import as MI, toolbar as TB, properties as P)
from ac9_cloth_sim.core import spec as S, draft as DR        # noqa: E402


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def mesh(name):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (.1, 0, 0), (0, 0, .1)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


class EyedropperFields(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = bpy.context
        spec = S.new_spec('Eyedropper')
        DR.add_polygon(spec, [[0, 0], [30, 0], [0, 30]], name='A')
        ST.new_text(ctx, spec['name'])
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        spec, outlines = ST.get(ctx)
        PV.rebuild(ctx, spec, outlines)
        cls.props = ctx.scene.ac9_cloth_sim
        cls.body = mesh('ED_body')
        cls.props.body = cls.body
        cls.cube = mesh('ED_cube')
        cls.placed = mesh('ED_placed')
        cls.placed[PLV.PROP] = 'A'
        cls.piece = PV.piece_objects()['A']

    def test_collision_add(self):
        self.props.collider_pick = self.cube
        self.assertIn(D.COLLIDER_PROP, self.cube)
        self.assertIsNone(self.props.collider_pick)
        self.assertIn(self.cube, D.collider_objects(bpy.context))

    def test_collision_poll(self):
        self.assertFalse(P._collider_poll(self.props, self.piece))
        self.assertFalse(P._collider_poll(self.props, self.placed))
        self.assertFalse(P._collider_poll(self.props, self.body))
        self.assertTrue(P._collider_poll(self.props, self.cube))

    def test_own_button(self):
        self.props.button_style_pick = self.cube
        self.assertIsNone(self.props.button_style_pick)
        self.assertEqual(bpy.context.scene.ac9_button_style, 'own_ed_cube')
        styles = ST.get(bpy.context)[0].get('button_styles') or []
        self.assertTrue(any(s.get('mesh') == 'ED_cube' for s in styles))
        self.assertFalse(P._style_poll(self.props, self.piece))

    def test_from_mesh_source(self):
        self.props.from_mesh_object = self.cube
        self.assertEqual(MI.source_mesh(bpy.context), self.cube)
        self.props.from_mesh_object = None
        bpy.context.view_layer.objects.active = self.placed
        self.assertEqual(MI.source_mesh(bpy.context), self.placed)
        self.assertFalse(P._from_mesh_poll(self.props, self.body))
        self.assertFalse(P._from_mesh_poll(self.props, self.piece))

    def test_whole_pieces_takes_obstacles(self):
        self.assertIn('OBSTACLE', TB.WORKS['TRANSFORM_PATTERN'][2])

    def test_pick_object_registered(self):
        self.assertTrue(hasattr(bpy.ops.ac9_cloth_sim, 'pick_object'))
        self.assertIn(TB.AC9_OT_SimPickObject, TB.CLASSES)


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[str(t[0]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-400:]) for t in res.errors])), 'utf-8')
