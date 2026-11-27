"""Three authored skin points select exactly one closed section before taking the taut hull."""
import unittest
import sys
import json
import importlib.util
from pathlib import Path
if __name__ == '__main__':
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('ac9_cloth_sim',root/'__init__.py',submodule_search_locations=[str(root)])
    addon=importlib.util.module_from_spec(spec);sys.modules['ac9_cloth_sim']=addon;spec.loader.exec_module(addon)
import numpy as np
from ac9_cloth_sim.core import measure as ME, body_axes as BA
try:
    import bpy
except ImportError:
    bpy = None


def cube(offset=(0, 0, 0), open_top=False):
    P = np.array([[-100,-100,-100], [100,-100,-100], [100,100,-100], [-100,100,-100],
                  [-100,-100,100], [100,-100,100], [100,100,100], [-100,100,100]], float) + offset
    T = np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                  [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]])
    if open_top:
        T = T[4:]
    return P, T


class ThreePoints(unittest.TestCase):
    def test_level_girth_and_order(self):
        body = BA.Body(*cube())
        P = np.array([[-100,0,0],[0,-100,0],[100,0,0]], float)
        for pts in (P, P[::-1], np.roll(P, 1, axis=0)):
            r = ME.tape_around_points(body, pts)
            self.assertAlmostEqual(r[0], 800, places=5)
            np.testing.assert_allclose(r[2][:,2], 0, atol=1e-6)

    def test_sloping_plane_passes_all_three_points(self):
        body = BA.Body(*cube())
        P = np.array([[-100,0,-50],[0,-100,0],[100,0,50]], float)
        o, n = ME.three_point_plane(P)
        r = ME.tape_around_points(body, P)
        self.assertIsNotNone(r)
        np.testing.assert_allclose((r[2]-o) @ n, 0, atol=1e-6)
        self.assertGreater(r[0], 800)

    def test_degenerate_nonfinite_and_nearly_collinear(self):
        for P in ([[0,0,0]]*3, [[0,0,0],[1,0,0],[2,0,0]],
                  [[0,0,0],[100,0,0],[200,.001,0]], [[0,0,0],[1,0,0],[0,float('nan'),0]]):
            self.assertIsNone(ME.three_point_plane(P))

    def test_disconnected_limb_points_do_not_select_first_limb(self):
        a, ta = cube(); b, tb = cube((400,0,0))
        body = BA.Body(np.vstack([a,b]), np.vstack([ta,tb+len(a)]))
        self.assertIsNone(ME.tape_around_points(body, [[-100,0,0],[0,-100,0],[400,-100,0]]))
        self.assertAlmostEqual(ME.tape_around_points(body, [[300,0,0],[400,-100,0],[500,0,0]])[0],800)

    def test_tangent_or_open_section_rejected(self):
        body = BA.Body(*cube(open_top=True))
        # Vertical section crosses the missing caps; no closed loop exists.
        self.assertIsNone(ME.tape_around_points(body, [[-100,0,-50],[-100,0,50],[100,0,0]]))
        self.assertIsNone(ME.tape_around_points(BA.Body(*cube()), [[-50,-50,100],[50,-50,100],[0,50,100]]))

    def test_legacy_one_point_result_unchanged(self):
        r = ME.tape_around(BA.Body(*cube()), [100,0,0], [0,0,1])
        self.assertAlmostEqual(r[0],800)


@unittest.skipIf(bpy is None, 'Blender storage and UI visibility regression')
class BlenderStorage(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace as NS
        from ac9_cloth_sim import measure_tool as MT
        self.MT = MT
        P, T = cube()
        self.mesh = bpy.data.meshes.new('tape_test')
        self.mesh.from_pydata(P * .001, [], T)
        self.ob = bpy.data.objects.new('tape_test', self.mesh)
        bpy.context.scene.collection.objects.link(self.ob)
        self.previous = MT._body(bpy.context)
        bpy.context.scene.ac9_cloth_sim.body = self.ob
        self.closed = NS(ob=self.ob, geo='closed', body=BA.Body(P,T), gd={'torso':{'o':[0,0,0]}})
        self.opened = NS(ob=self.ob, geo='open', body=BA.Body(*cube(open_top=True)))
        self.tape = dict(name='Regression',kind='around',method='THREE_POINTS',
                         pts=[[-100,0,-50],[-100,0,50],[100,0,0]])
        self.data = {}
        self.assertTrue(MT.store_tape(self.ob,self.closed,self.tape,self.data))

    def tearDown(self):
        bpy.context.scene.ac9_cloth_sim.body = self.previous
        bpy.data.objects.remove(self.ob,do_unlink=True)
        bpy.data.meshes.remove(self.mesh)
        self.MT._cache.update(key=None,cache=None)

    def test_failed_remeasurement_clears_value_and_line_then_recovers(self):
        self.MT.remeasure_all(bpy.context,self.ob,self.data,self.opened)
        self.assertIsNone(self.tape['value'])
        self.assertTrue(self.tape['invalid'])
        self.assertNotIn('Regression',self.MT.read(self.ob,self.MT.GEO).get('tapes',{}))
        self.MT.remeasure_all(bpy.context,self.ob,self.data,self.closed)
        self.assertAlmostEqual(self.tape['value'],800,places=3)
        self.assertNotIn('invalid',self.tape)

    def test_failed_drag_does_not_publish_invalid_definition(self):
        import copy
        before=self.ob[self.MT.PROP],self.ob[self.MT.GEO]
        candidate=copy.deepcopy(self.tape)
        self.assertFalse(self.MT.store_tape(self.ob,self.opened,candidate,self.data))
        self.assertEqual(before,(self.ob[self.MT.PROP],self.ob[self.MT.GEO]))

    def test_mirror_name_follows_mirrored_points(self):
        from ac9_cloth_sim import measure_edit as MEE
        MEE.select(bpy.context,'tape:Regression')
        old=self.MT.body_cache
        self.MT.body_cache=lambda *a,**kw:self.closed
        try:receipt=bpy.ops.ac9_cloth_sim.measure_mirror()
        finally:self.MT.body_cache=old
        self.assertIn('FINISHED',receipt)
        mirrored=self.MT.read(self.ob)['tapes'][-1]
        self.assertGreater(mirrored['pts'][0][0],0)
        self.assertTrue(mirrored['name'].endswith('(L)'))


if __name__ == '__main__':
    if bpy is not None:
        addon.register()
    run=unittest.main(argv=['tape'],exit=False)
    if '--' in sys.argv:
        # The first argument is the checkout/package parent; the runner's
        # second argument is this test's evidence file (as with other tests).
        output=Path(sys.argv[sys.argv.index('--')+2])
        output.write_text(json.dumps(dict(run=run.result.testsRun,ok=run.result.wasSuccessful())),encoding='utf-8')
    if not run.result.wasSuccessful():
        raise SystemExit(1)
