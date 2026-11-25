# SPDX-License-Identifier: GPL-3.0-or-later
"""A placed object is made with its origin on the piece (placed_view.build calls center_control_origins), not only
after Move by Hand: a placed piece clicked and turned with R turns about its own middle. The world shape stays where
it was made (not "moved"), and a pose read from the object puts it back where it is.

Run: blender -b --factory-startup --python tests/test_placed_origin.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import math
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import numpy as np                                           # noqa: E402
from mathutils import Matrix                                 # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import dress as DR, placed_view as PLV, store as ST  # noqa: E402
from ac9_cloth_sim.core import spec as S, draft as D        # noqa: E402


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def world(ob):
    me = ob.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get('co', co)
    return (np.c_[co.reshape(-1, 3), np.ones(len(me.vertices))] @ np.array(ob.matrix_world).T)[:, :3]


class PlacedOrigin(unittest.TestCase):
    def setUp(self):
        ctx = bpy.context
        self.previous_body = ctx.scene.ac9_cloth_sim.body
        bpy.ops.mesh.primitive_cube_add(size=.1)
        self.body = ctx.object
        ctx.scene.ac9_cloth_sim.body = self.body
        # a piece 300 mm wide bent round a 100 mm cylinder: the middle of its box lies off the piece
        self.sp = S.new_spec('PlacedOrigin')
        D.add_polygon(self.sp, [[0, 0], [300, 0], [300, 200], [0, 200]], name='A')
        self.sp['placement'] = {'A': dict(guide='torso', theta=90., h=0., anchor=[150, 100])}
        self.sp['guides'] = {'torso': dict(o=[0, 0, 0], R=np.eye(3).tolist(), r=100, h0=-200, h1=200, points=[])}
        self.text = ST.new_text(ctx, self.sp['name'])
        ST.write(ctx, self.sp, keep=True)
        res = DR.prepare(ctx, 20., shapes_only=True, placed_only=True)
        PLV.build(ctx, ST.get(ctx)[0], res['pat'], res['tris'], res['X'], res['pairs'])
        self.ob = PLV.objects(ST.get(ctx)[0])['A']

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

    def test_origin_on_the_piece(self):
        W = world(self.ob)
        mid = W.mean(0)
        box_mid = 0.5 * (W.min(0) + W.max(0))
        self.assertGreater(np.linalg.norm(box_mid - mid) * 1000.0, 5.0)        # the case it is about
        self.assertLess(np.linalg.norm(np.array(self.ob.matrix_world.translation) - mid) * 1000.0, 0.01)

    def test_made_shape_not_moved(self):
        self.assertFalse(PLV.is_moved(self.ob))
        self.assertIsNone(PLV.pose_of(self.ob))

    def test_pose_round_trip(self):
        M0 = self.ob.matrix_world.copy()
        self.ob.matrix_world = (Matrix.Translation(M0.translation) @ Matrix.Rotation(math.radians(30), 4, 'Z')
                                @ Matrix.Translation(-M0.translation) @ M0)
        bpy.context.view_layer.update()
        before = world(self.ob)
        mid0 = before.mean(0)
        pose = PLV.pose_of(self.ob)
        self.assertIsNotNone(pose)
        self.assertTrue(PLV.is_moved(self.ob))
        self.ob.matrix_world = PLV.matrix_for_pose(self.ob, pose)
        bpy.context.view_layer.update()
        self.assertLess(np.abs(world(self.ob) - before).max() * 1000.0, 0.01)
        # turned about its origin: its middle stayed where it was
        self.assertLess(np.linalg.norm(mid0 - np.array(M0.translation)) * 1000.0, 0.01)


class PairFollowerKept(unittest.TestCase):
    """A pair shown as two objects (meshed differently): the leader's origin moved onto its piece must not carry the
    follower (which copies the leader's matrix, mirrored) off its shape. It did, by the shift mirrored, and the
    follower then counted as moved by hand, so placing again never made it again."""
    def setUp(self):
        ctx = bpy.context
        self.previous_body = ctx.scene.ac9_cloth_sim.body
        bpy.ops.mesh.primitive_cube_add(size=.1)
        self.body = ctx.object
        ctx.scene.ac9_cloth_sim.body = self.body
        self.sp = S.new_spec('PairFollower')
        D.add_polygon(self.sp, [[0, 0], [300, 0], [300, 200], [0, 200]], name='A')
        # the mirror image with one more outline point: meshed differently, so two objects, one following
        D.add_polygon(self.sp, [[500, 0], [800, 0], [800, 200], [650, 200], [500, 200]], name='B')
        self.sp['pairs'] = [['A', 'B']]
        self.sp['placement'] = {'A': dict(guide='torso', theta=40., h=0., anchor=[150, 100]),
                                'B': dict(guide='torso', theta=140., h=0., anchor=[650, 100])}
        self.sp['guides'] = {'torso': dict(o=[0, 0, 0], R=np.eye(3).tolist(), r=100, h0=-200, h1=200, points=[])}
        self.sp['placement'], _ = S.mirror_twins(self.sp, sources={'A'})
        self.text = ST.new_text(ctx, self.sp['name'])
        ST.write(ctx, self.sp, keep=True)
        res = DR.prepare(ctx, 20., shapes_only=True, placed_only=True)
        PLV.build(ctx, ST.get(ctx)[0], res['pat'], res['tris'], res['X'], res['pairs'])
        self.obs = PLV.objects(ST.get(ctx)[0])

    tearDown = PlacedOrigin.tearDown

    def test_follower_on_its_shape(self):
        a, b = self.obs['A'], self.obs['B']
        self.assertIsNot(a, b)
        fol = a if a.get(PLV.LEAD_PROP) else b
        lead = b if fol is a else a
        self.assertTrue(fol.get(PLV.LEAD_PROP))                              # the case it is about
        self.assertGreater(np.abs(np.asarray(lead.get(PLV.ORIGIN_SHIFT_PROP, (0, 0, 0)))).max() * 1000.0, 5.0)
        for ob in (lead, fol):
            self.assertFalse(PLV.moved_since_made(ob), ob.name)


if __name__ == '__main__':
    r = unittest.main(argv=[sys.argv[0]], exit=False).result
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps({'run': r.testsRun, 'failures': [str(f[0]) for f in r.failures],
                                             'errors': [str(e[0]) + e[1][-600:] for e in r.errors]}),
                                 encoding='utf-8')
