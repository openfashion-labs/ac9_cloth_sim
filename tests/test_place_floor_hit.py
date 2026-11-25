# SPDX-License-Identifier: GPL-3.0-or-later
"""Real evaluated ray casts for Place; no solver, mouse or GPU substitutes.

Only the screen-to-ray conversion is replaced. Synthetic finite faces exercise
gaps, hidden pieces, holes, transforms and coplanar surfaces independently.
"""
import sys,json,types,time,unittest,importlib.util
from pathlib import Path
from unittest.mock import patch
import bpy
import numpy as np
from mathutils import Matrix,Vector

ARGS=sys.argv[sys.argv.index('--')+1:]
ROOT=Path(ARGS[0]);OUT=Path(ARGS[1]);OUT.parent.mkdir(parents=True,exist_ok=True)
if 'ac9_cloth_sim' not in sys.modules:
    spec=importlib.util.spec_from_file_location('ac9_cloth_sim',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
import ac9_cloth_sim as A
from ac9_cloth_sim import placement as PL,toolbar as TB,pattern_view as PV,store as ST,pre_click as PRE,view_visibility as VV
from ac9_cloth_sim.core import spec as S
A.register()

class FloorHit(unittest.TestCase):
    def setUp(self):
        for ob in list(bpy.data.objects):bpy.data.objects.remove(ob,do_unlink=True)
        self.spec=S.new_spec('FloorHit')
        ST.new_text(bpy.context,'FloorHit');ST.text(bpy.context).from_string(S.dumps(self.spec));ST.invalidate()
        self.layout=PV.layout_empty(bpy.context,create=True)
        self.window=bpy.context.window_manager.windows[0]
        self.area=next(a for a in self.window.screen.areas if a.type=='VIEW_3D')
        self.region=next(r for r in self.area.regions if r.type=='WINDOW')
        self.ctx=self.enterContext(bpy.context.temp_override(window=self.window,area=self.area,region=self.region))
        self.area.spaces.active.use_local_collections=False

    def mesh(self,name,verts,faces,pattern=False,placed=False):
        verts=np.asarray(verts,float)
        self.assertTrue(np.isfinite(verts).all())
        me=bpy.data.meshes.new(name);me.from_pydata(verts.tolist(),[],faces);me.update()
        self.assertTrue(all(p.area>1e-12 for p in me.polygons))
        ob=bpy.data.objects.new(name,me)
        (PV._collection(bpy.context) if pattern else bpy.context.scene.collection).objects.link(ob)
        if pattern:
            ob[PV.PIECE_PROP]=name
            # The old bbox path reads the document, so include the actual outline.
            self.spec['pieces'].append(dict(name=name,enabled=True,edges=[dict(id='E',pts=(np.vstack((verts[:,:2],verts[:1,:2]))*1000).tolist())],internals=[]))
            ST.text(bpy.context).from_string(S.dumps(self.spec));ST.invalidate()
        if placed:ob['ac9_placed']=name
        bpy.context.view_layer.update()
        return ob

    def quad(self,name,x0,y0,x1,y1,z=0,**kw):
        return self.mesh(name,[(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z)],[(0,1,2,3)],**kw)

    def hit(self,xy=(0,0),origin=None,direction=(0,0,-1)):
        o=Vector(origin if origin is not None else (xy[0],xy[1],2));d=Vector(direction).normalized()
        with patch.object(PL,'region_2d_to_origin_3d',return_value=o),patch.object(PL,'region_2d_to_vector_3d',return_value=d):
            return PL.flat_hit(bpy.context,0,0)

    def floor(self,xy=(0,0)):
        hit=self.hit(xy);self.assertIsNotNone(hit)
        self.assertEqual(hit[2],PL.GROUND)
        np.testing.assert_allclose(hit[1]['o'],[xy[0]*1000,xy[1]*1000,PL.LIFT_FLAT],atol=1e-3)

    def test_distant_piece_does_not_fill_gap_or_change_floor(self):
        self.quad('main',2.5,-.5,3.6,.35,pattern=True)
        for p in [(0,0),(.60009,0),(-.60009,0)]:self.floor(p)
        remote=self.quad('remote',-.538,1.016,-.521,1.035,pattern=True)
        for p in [(0,0),(.60009,0),(-.60009,0),(1.5,0)]:self.floor(p)
        remote.hide_set(True)
        for p in [(0,0),(.60009,0),(-.60009,0)]:self.floor(p)

    def test_real_face_blocks_and_hidden_face_does_not(self):
        ob=self.quad('pattern',-.2,-.2,.2,.2,pattern=True)
        self.assertIsNone(self.hit())
        ob.hide_set(True);bpy.context.view_layer.update();self.floor()

    def test_layout_translation_does_not_block_empty_ground(self):
        self.quad('distant',2,0,3,1,pattern=True);self.floor()
        self.layout.location.x=10;bpy.context.view_layer.update();self.floor()

    def test_concave_notch_is_empty(self):
        self.mesh('concave',[(0,0,0),(1,0,0),(1,.3,0),(.3,.3,0),(.3,1,0),(0,1,0)],[(0,1,2,3,4,5)],pattern=True)
        self.floor((.7,.7));self.assertIsNone(self.hit((.1,.7)))

    def test_hole_has_no_pattern_face(self):
        points=[(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0),(-.2,-.2,0),(.2,-.2,0),(.2,.2,0),(-.2,.2,0)]
        faces=[(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
        self.mesh('hole',points,faces,pattern=True)
        self.floor();self.assertIsNone(self.hit((.5,0)))

    def test_transformed_pattern_blocks_only_its_evaluated_face(self):
        ob=self.quad('tilted',-.2,-.2,.2,.2,pattern=True)
        ob.matrix_world=Matrix.Translation((.4,.3,.6))@Matrix.Rotation(.5,4,'X')@Matrix.Diagonal((-2.,.7,1.,1.))
        bpy.context.view_layer.update()
        self.assertIsNone(self.hit((.4,.3)));self.floor((-1,0))
        ob.hide_set(True);self.floor((.4,.3))

    def test_coplanar_floor_never_wins_over_visible_pattern(self):
        self.quad('pattern',-.3,-.3,.3,.3,pattern=True)
        self.quad('floor',-20,-20,20,20)
        for i in range(600):
            x=-.25+.5*((i%30)/29);y=-.25+.5*((i//30)/19)
            target=Vector((x,y,0));origin=target+Vector((.4,-.3,2))
            self.assertIsNone(self.hit(origin=origin,direction=target-origin))

    def test_visible_support_in_front_of_pattern_is_used(self):
        self.quad('pattern',-.3,-.3,.3,.3,pattern=True)
        self.quad('support',-.4,-.4,.4,.4,z=.5)
        hit=self.hit();self.assertIsNotNone(hit);self.assertEqual(hit[2],'support')
        self.assertAlmostEqual(hit[1]['o'][2],502,places=3)

    def test_placed_object_is_transparent_to_floor_hit(self):
        self.quad('placed',-.3,-.3,.3,.3,z=.5,placed=True)
        self.floor()

    def test_guide_hover_and_gesture_share_body_visibility(self):
        body=self.quad('body',5,5,6,6)
        bpy.context.scene.ac9_cloth_sim.body=body
        spec=dict(self.spec,guides={'torso':dict(o=[0,0,0],R=np.eye(3).tolist(),r=100,h0=0,h1=500)})
        ST.text(bpy.context).from_string(S.dumps(spec));ST.invalidate()
        op=types.SimpleNamespace(spec=spec,guides=spec['guides'],kind=None,rel_guide=None,_drag_guide=None,ctrl=True)
        o=Vector((0,-1,.2));d=Vector((0,1,0));event=types.SimpleNamespace(mouse_region_x=0,mouse_region_y=0)
        with patch.object(PL,'region_2d_to_origin_3d',return_value=o),patch.object(PL,'region_2d_to_vector_3d',return_value=d),patch.object(TB,'region_2d_to_origin_3d',return_value=o),patch.object(TB,'region_2d_to_vector_3d',return_value=d):
            self.assertTrue(TB._guide_under(bpy.context,event));self.assertEqual(PL.AC9_OT_SimPlace._hit(op,bpy.context,0,0,snap=False)[0],'torso')
            body.hide_set(True);bpy.context.view_layer.update()
            self.assertFalse(TB._guide_under(bpy.context,event));self.assertIsNone(PL.AC9_OT_SimPlace._hit(op,bpy.context,0,0,snap=False))
            with patch.object(PRE,'_set') as mark:
                PRE.place_guide(bpy.context,event);self.assertTrue(mark.called);self.assertFalse(mark.call_args.kwargs)

if __name__=='__main__':
    with (OUT.with_suffix('.txt')).open('w',encoding='utf8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FloorHit))
    OUT.write_text(json.dumps(dict(run=result.testsRun,failures=[(str(t),err) for t,err in result.failures],errors=[(str(t),err) for t,err in result.errors]),indent=2),encoding='utf8')
    if not result.wasSuccessful():raise RuntimeError('Place floor regression failed; see '+str(OUT))
