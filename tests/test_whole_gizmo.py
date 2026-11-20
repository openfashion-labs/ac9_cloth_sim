# SPDX-License-Identifier: GPL-3.0-or-later
"""Whole Pieces' gizmo shows one kind at a time, move or rotate (user 2026-11-19: together the rotate rings hid the
move arrows but their tips): the scene's whole_gizmo sets the view's Object Gizmos, W switches it, and the Keys list
says so.

Run: blender -b --factory-startup --python tests/test_whole_gizmo.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import work_screen as WS, draft_tool as DT  # noqa: E402


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def a_view_space():
    for sc in bpy.data.screens:
        for a in sc.areas:
            if a.type == 'VIEW_3D':
                return a.spaces.active
    raise unittest.SkipTest('no 3D view in the startup file')


class WholeGizmo(unittest.TestCase):
    def test_one_kind_at_a_time(self):
        sp, scene = a_view_space(), bpy.context.scene
        for mode, move, rot in (('ROTATE', False, True), ('MOVE', True, False)):
            scene.ac9_cloth_sim.whole_gizmo = mode
            WS._set_gizmo(sp, scene)
            self.assertEqual((sp.show_gizmo_object_translate, sp.show_gizmo_object_rotate,
                              sp.show_gizmo_object_scale), (move, rot, False), mode)

    def test_default_is_move(self):
        self.assertEqual(type(bpy.context.scene.ac9_cloth_sim).bl_rna.properties['whole_gizmo'].default, 'MOVE')

    def test_w_switches_it(self):
        rows = [r for r in DT.AC9_WT_TransformPattern.bl_keymap if r[1].get('type') == 'W']
        self.assertEqual(len(rows), 1)
        idname, ev, kw = rows[0]
        self.assertEqual(idname, 'wm.context_toggle_enum')
        props = dict(kw['properties'])
        self.assertEqual(props['data_path'], 'scene.ac9_cloth_sim.whole_gizmo')
        self.assertEqual({props['value_1'], props['value_2']}, {'MOVE', 'ROTATE'})
        # the path resolves and the operator flips it
        bpy.context.scene.ac9_cloth_sim.whole_gizmo = 'MOVE'
        bpy.ops.wm.context_toggle_enum(data_path=props['data_path'], value_1='MOVE', value_2='ROTATE')
        self.assertEqual(bpy.context.scene.ac9_cloth_sim.whole_gizmo, 'ROTATE')
        bpy.context.scene.ac9_cloth_sim.whole_gizmo = 'MOVE'

    def test_keys_list_has_w(self):
        self.assertIn('W', [k for k, _ in DT.TRANSFORM_KEYS])


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[str(t[0]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-400:]) for t in res.errors])), 'utf-8')
