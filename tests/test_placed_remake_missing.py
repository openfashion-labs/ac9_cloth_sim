# SPDX-License-Identifier: GPL-3.0-or-later
"""Adjusted Shapes are made again when they go missing (placement.remake_missing, called when the guides are found
again): a placed piece without its object could not be clicked in 3D, and no button makes them any more. The hand
move kept in the spec comes back with the object.

Run: blender -b --factory-startup --python tests/test_placed_remake_missing.py -- <parent of ac9_cloth_sim> [out.json]
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
from ac9_cloth_sim import placed_view as PLV, placement as PL, store as ST  # noqa: E402
from ac9_cloth_sim.core import spec as S, draft as D        # noqa: E402


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class RemakeMissing(unittest.TestCase):
    def setUp(self):
        ctx = bpy.context
        self.previous_body = ctx.scene.ac9_cloth_sim.body
        bpy.ops.mesh.primitive_cube_add(size=.1)
        self.body = ctx.object
        ctx.scene.ac9_cloth_sim.body = self.body
        self.sp = S.new_spec('RemakeMissing')
        D.add_polygon(self.sp, [[0, 0], [200, 0], [200, 150], [0, 150]], name='A')
        D.add_polygon(self.sp, [[300, 0], [500, 0], [500, 150], [300, 150]], name='B')
        self.sp['placement'] = {'A': dict(guide='torso', theta=0., h=0., anchor=[100, 75]),
                                'B': dict(guide='torso', theta=180., h=0., anchor=[400, 75],
                                          pose={'loc': [0.0, 0.0, 30.0], 'rot': [0.0, 0.0, 0.0]})}
        self.sp['guides'] = {'torso': dict(o=[0, 0, 0], R=np.eye(3).tolist(), r=120, h0=-200, h1=200, points=[])}
        self.text = ST.new_text(ctx, self.sp['name'])
        ST.write(ctx, self.sp, keep=True)
        # made the way the add-on makes them (Place / show_placed), so a remake compares like with like
        PL.show_placed(ctx, placed_only=True, report=lambda *_a: None)

    def tearDown(self):
        sp = ST.get(bpy.context)[0]
        PLV.remove(list(PLV.objects(sp)), sp)
        bpy.context.scene.ac9_cloth_sim.body = self.previous_body
        me = self.body.data
        bpy.data.objects.remove(self.body, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        bpy.data.texts.remove(self.text)
        ST._discard_cache()

    def z_mm(self, name):
        ob = PLV.objects(ST.get(bpy.context)[0])[name]
        me = ob.data
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get('co', co)
        W = (np.c_[co.reshape(-1, 3), np.ones(len(me.vertices))] @ np.array(ob.matrix_world).T)[:, :3]
        return W[:, 2].mean() * 1000.0

    def test_made_again_with_the_hand_move(self):
        ctx = bpy.context
        before = {n: self.z_mm(n) for n in ('A', 'B')}
        self.assertGreater(before['B'] - before['A'], 20.0)          # B's pose lifts it 30 mm
        sp = ST.get(ctx)[0]
        names = sorted(PLV.objects(sp))
        PLV.remove(names, sp)
        self.assertFalse(PLV.objects(ST.get(ctx)[0]))
        notes = PL.remake_missing(ctx, ST.get(ctx)[0], names)
        self.assertEqual(notes, [])
        self.assertEqual(sorted(PLV.objects(ST.get(ctx)[0])), ['A', 'B'])
        for n in ('A', 'B'):
            self.assertLess(abs(self.z_mm(n) - before[n]), 0.01, n)

    def test_no_move_by_hand_operator(self):
        with self.assertRaises(KeyError):
            bpy.ops.ac9_cloth_sim.move_by_hand.get_rna_type()


if __name__ == '__main__':
    res = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps(dict(run=res.testsRun, failures=[str(t[0]) for t in res.failures],
                                                 errors=[(str(t[0]), t[1][-800:]) for t in res.errors])), encoding='utf-8')
    import os
    os._exit(0 if res.wasSuccessful() else 1)
