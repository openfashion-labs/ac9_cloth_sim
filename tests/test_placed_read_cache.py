# SPDX-License-Identifier: GPL-3.0-or-later
"""The adjusted shapes' draw caches: a placed object is read again only when what it depends on changed (its geometry
epoch from the depsgraph handler, its matrix, its tags, its modifiers' switches), and the seam weights are kept by the
layout they were made from, not by the mesh datablock (a pair's half clicked swaps the mesh for its mirrored copy:
the seams were recomputed every time, 1.3 s with the whole-read of every mesh per redraw).

Run: blender -b --factory-startup --python tests/test_placed_read_cache.py -- <parent of ac9_cloth_sim> [out.json]
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
from ac9_cloth_sim import placed_view as PLV, overlay as OV  # noqa: E402


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def placed(name):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (.1, 0, 0), (0, .1, 0), (.1, .1, 0)], [], [(0, 1, 2), (1, 3, 2)])
    a = me.attributes.new(PLV.FLAT_ATTR, 'FLOAT2', 'POINT')
    a.data.foreach_set('vector', np.array([0, 0, 100, 0, 0, 100, 100, 100], np.float32))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob[PLV.PROP] = 'A'
    return ob


class ReadCache(unittest.TestCase):
    def setUp(self):
        PLV.invalidate_shown()
        self.ob = placed('RC_placed')
        bpy.context.view_layer.update()

    def tearDown(self):
        bpy.data.objects.remove(self.ob, do_unlink=True)

    def read(self):
        return PLV._read(bpy.context, self.ob, bpy.context.evaluated_depsgraph_get())

    def test_unchanged_object_is_not_read_again(self):
        self.assertTrue(OV.watching())
        c = self.read()
        self.assertIsNotNone(c.get('stamp'))
        c['marker'] = 1
        self.assertIs(self.read(), c)            # the stamp matched: the same entry, not read again
        self.assertEqual(self.read().get('marker'), 1)

    def test_moved_object_is_read_again(self):
        c = self.read()
        W0 = c['W'].copy()
        self.ob.location.x += 0.5
        bpy.context.view_layer.update()
        W1 = self.read()['W']
        self.assertAlmostEqual(float(W1[0, 0] - W0[0, 0]), 0.5, places=5)

    def test_edited_vertices_are_read_again(self):
        self.read()
        co = np.empty(12)
        self.ob.data.vertices.foreach_get('co', co)
        co[0] = -0.2
        self.ob.data.vertices.foreach_set('co', co)
        self.ob.data.update()
        bpy.context.view_layer.update()
        self.assertAlmostEqual(float(self.read()['W'][0, 0]), -0.2, places=5)

    def test_frame_changes_the_stamp(self):
        # a frame change re-evaluates without depsgraph_update_post: an armature or a hook can move the piece
        sc = bpy.context.scene
        f0 = sc.frame_current
        s0 = PLV._stamp(self.ob)
        sc.frame_set(f0 + 1)
        try:
            self.assertNotEqual(PLV._stamp(self.ob), s0)
        finally:
            sc.frame_set(f0)

    def test_undo_generation_changes_the_stamp(self):
        s0 = PLV._stamp(self.ob)
        OV._datablocks_swapped()
        self.assertNotEqual(PLV._stamp(self.ob), s0)

    def test_no_stamp_without_the_handler(self):
        hs = bpy.app.handlers.depsgraph_update_post
        hs.remove(OV._geometry_changed)
        try:
            self.assertIsNone(PLV._stamp(self.ob))
        finally:
            hs.append(OV._geometry_changed)


class SeamWeights(unittest.TestCase):
    F = np.array([[0, 0], [100, 0], [0, 100], [100, 100]], float)
    T = np.array([[0, 1, 2], [1, 3, 2]])
    W = np.array([[0, 0, 0], [.1, 0, 0], [0, .1, 0], [.1, .1, 0]], float)
    P = np.array([[25.0, 25.0], [75.0, 60.0]])

    def setUp(self):
        PLV._bary.clear()

    def test_same_layout_other_mesh_reuses_weights(self):
        a = PLV.to_world((self.F, self.T, self.W, ('obj', 'key1')), self.P)
        self.assertEqual(len(PLV._bary), 1)
        # the mirrored copy: another datablock, the layout within float32 of the same
        F2 = self.F + 2e-4
        b = PLV.to_world((F2, self.T[:, ::-1], self.W, ('obj_R', 'key2')), self.P)
        self.assertEqual(len(PLV._bary), 1)
        np.testing.assert_allclose(a - np.array([2e-7,2e-7,0.]), b, atol=1e-9)
        np.testing.assert_allclose(a[0], [0.025, 0.025, 0], atol=1e-9)

    def test_changed_layout_recomputes(self):
        PLV.to_world((self.F, self.T, self.W, ('obj', 'k')), self.P)
        F2 = self.F * 2.0
        b = PLV.to_world((F2, self.T, self.W, ('obj', 'k')), self.P)
        np.testing.assert_allclose(b[0], [0.0125, 0.0125, 0], atol=1e-9)

    def test_invalidate_shown_keeps_weights(self):
        PLV.to_world((self.F, self.T, self.W, ('obj', 'k')), self.P)
        PLV.invalidate_shown()
        self.assertEqual(len(PLV._bary), 1)


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[str(t[0]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-400:]) for t in res.errors])), 'utf-8')
