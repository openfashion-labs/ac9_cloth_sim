# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-storage control contracts. Synthetic finite triangles, no physical simulation or GPU."""
import sys,json,unittest,tempfile
from pathlib import Path
import numpy as np
import bpy
from mathutils import Matrix
ARGS=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
sys.path.insert(0,ARGS[0])
import ac9_cloth_sim as A
from ac9_cloth_sim import control_basis as CB,store as ST,dress as D,piece_move as PM,selection as SEL,placement as PL,placed_view as PV,display_state as DS,work_screen as WS
from ac9_cloth_sim.core import spec as S,draft as DR
from ac9_cloth_sim import pattern_view as PATTERN
A.register()
OUT=Path(ARGS[1]);OUT.parent.mkdir(parents=True,exist_ok=True)

class ControlBasis(unittest.TestCase):
    def setUp(self):
        PM._busy[0]=True
        self.sp=S.new_spec('ControlBasis')
        DR.add_polygon(self.sp,[[0,0],[40,0],[0,40]],name='A')
        DR.add_polygon(self.sp,[[100,0],[140,0],[100,40]],name='B')
        self.sp['pairs']=[['A','B']]
        self.sp['guides']={'torso':{'o':[0,0,0],'R':np.eye(3).tolist(),'r':100,'h0':0,'h1':100}}
        self.sp['placement']={n:{'guide':'torso','anchor':[0,0],'theta':40. if n=='A' else 140.,'h':0.} for n in ['A','B']}
        ST.new_text(bpy.context,self.sp['name']);ST.write(bpy.context,self.sp,keep=True)
        PATTERN.rebuild(bpy.context,self.sp,{p['name']:S.Outline(p) for p in self.sp['pieces']},switch_document=True)
        self.assertTrue(PATTERN.integrity(bpy.context,self.sp)['ok'])
        body=bpy.data.objects.get('ControlBasisBody')
        if body is None:
            me=bpy.data.meshes.new('ControlBasisBody');me.from_pydata([(10,0,0),(10.1,0,0),(10,.1,0),(10,0,.1)],[],[(0,2,1),(0,1,3),(0,3,2),(1,2,3)])
            body=bpy.data.objects.new('ControlBasisBody',me);bpy.context.scene.collection.objects.link(body)
        bpy.context.scene.ac9_cloth_sim.body=body
        self.flat=np.array([[0,0],[40,0],[0,40],[100,0],[140,0],[100,40]],float)
        self.F=np.array([[0,1,2],[3,5,4]]);self.fp=np.array([0,1]);self.X=np.array([[100,0,0],[140,0,0],[100,0,40],[-100,0,0],[-140,0,0],[-100,3,40]],float)
        self.g=D.garment_object(bpy.context,D.garment_name(self.sp),self.flat,self.F,self.fp,self.X,['A','B'],editing_basis={'A':CB.PLACED,'B':CB.PLACED})
        self.assertTrue(np.isfinite(self.X).all())
    def tearDown(self):
        PM.clear();PV.remove(['A','B'],ST.get(bpy.context)[0]);PM._state.clear()
        g=bpy.data.objects.get('AC9_Garment_ControlBasis')
        if g:bpy.data.objects.remove(g,do_unlink=True)
        t=ST.text(bpy.context)
        if t:
            for ob in list(PATTERN.piece_objects().values()):
                if ob.get('ac9_pattern_spec') == t:bpy.data.objects.remove(ob,do_unlink=True)
            bpy.data.texts.remove(t)
        ST.invalidate();PM._busy[0]=False
    def co(self):
        c=np.empty((len(self.g.data.vertices),3));self.g.data.vertices.foreach_get('co',c.ravel());return c*1000
    def move(self,names=['A'],T=None):
        h=PM.ensure_handle(bpy.context,self.g,names);self.assertIsNotNone(h)
        st=PM._state[h.name];h.matrix_world=(T or Matrix.Translation((.01,0,0)))@h.matrix_world
        PM._apply(bpy.context,h,st);return h,st
    def test_placement_pair_on_merged_mesh(self):
        before=self.co();self.move();d=self.co()-before
        np.testing.assert_allclose(d[:3],[[10,0,0]]*3,atol=.00003)
        np.testing.assert_allclose(d[3:],[[-10,0,0]]*3,atol=.00003)
        self.assertAlmostEqual(CB.project_pose(bpy.context,ST.get(bpy.context)[0])['placement']['A']['pose']['loc'][0],10,places=3)
    def test_result_moves_selected_side_only(self):
        CB.adopt(bpy.context,self.g);before=self.co();self.move();d=self.co()-before
        np.testing.assert_allclose(d[:3],[[10,0,0]]*3,atol=.00003);np.testing.assert_array_equal(d[3:],0)
        self.assertNotIn('pose',ST.get(bpy.context)[0]['placement']['A'])
    def test_b_side_leads(self):
        before=self.co();self.move(['B']);d=self.co()-before
        np.testing.assert_allclose(d[3:],[[10,0,0]]*3,atol=.00003);np.testing.assert_allclose(d[:3],[[-10,0,0]]*3,atol=.00003)
    def test_both_selected_pair_not_double_applied(self):
        before=self.co();self.move(['A','B']);d=self.co()-before
        np.testing.assert_allclose(d[:3],[[10,0,0]]*3,atol=.00003);np.testing.assert_allclose(d[3:],[[-10,0,0]]*3,atol=.00003)
    def test_unlinked_is_independent(self):
        sp=ST.get(bpy.context)[0];sp['pairs']=[];ST.put(bpy.context,sp);before=self.co();self.move();np.testing.assert_array_equal((self.co()-before)[3:],0)
    def test_rotation_uses_world_mirror_and_preserves_own_shape(self):
        import math
        before=self.co();T=Matrix.Rotation(math.radians(30),4,'Z');self.move(T=T)
        H=np.diag([-1.,1,1,1]);expected=before.copy()
        expected[:3]=(np.c_[before[:3]/1000,np.ones(3)]@np.array(T).T)[:,:3]*1000
        expected[3:]=(np.c_[before[3:]/1000,np.ones(3)]@(H@np.array(T)@H).T)[:,:3]*1000
        np.testing.assert_allclose(self.co(),expected,atol=.00003)
    def test_unknown_cannot_move_but_can_select(self):
        CB.publish(bpy.context,self.g,{});before=self.co()
        SEL.apply(bpy.context,['A'],'SET',active='A',view='world')
        self.assertIn('A',SEL.pieces(bpy.context));self.assertEqual(PM.handles(),[]);np.testing.assert_array_equal(before,self.co())
    def test_load_cannot_promote_initial_shape(self):
        before=self.co();D._relink_jobs();self.assertFalse(D.has_result(self.g));self.assertNotIn(D.JOB_MISSING_PROP,self.g);np.testing.assert_array_equal(before,self.co())
    def test_local_adoption_keeps_nonparticipant(self):
        CB.adopt(bpy.context,self.g,['A']);self.assertEqual(CB.basis(self.g),{'A':CB.RESULT,'B':CB.PLACED})
    def test_unchanged_output_can_be_adopted(self):
        before=self.co();CB.adopt(bpy.context,self.g);np.testing.assert_array_equal(before,self.co());self.assertTrue(D.has_result(self.g))
    def test_stale_mesh_handle_refuses_write(self):
        h=PM.ensure_handle(bpy.context,self.g,['A']);st=PM._state[h.name]
        self.g.data=self.g.data.copy();before=self.co();h.location.x+=.01
        with self.assertRaises(ValueError):PM._apply(bpy.context,h,st)
        np.testing.assert_array_equal(before,self.co())
    def test_placement_and_result_readers_agree(self):
        self.assertEqual(SEL._worn(bpy.context,self.sp)[0],set());self.assertEqual(PL.split_worn(self.sp,['A','B'])[1],[])
        CB.adopt(bpy.context,self.g);self.assertEqual(SEL._worn(bpy.context,self.sp)[0],{'A','B'});self.assertEqual(set(PL.split_worn(self.sp,['A','B'])[1]),{'A','B'})
    def test_pose_reset_preview_matches_commit_parameters(self):
        from types import SimpleNamespace
        p=dict(self.sp['placement']['A'],pose={'loc':[35,0,0],'rot':[0,0,0]})
        op=SimpleNamespace(rel={'A':p},press=('torso',40.,0.))
        pl=PL.AC9_OT_SimPlace._placements(op,('torso',50.,10.))['A']
        self.assertNotIn('pose',pl);self.assertEqual(pl['theta'],50.);self.assertEqual(pl['h'],10.)
    def test_gizmo_only_whole_pieces(self):
        sp=next(a.spaces.active for s in bpy.data.screens for a in s.areas if a.type=='VIEW_3D')
        WS._set_gizmo(sp,bpy.context.scene,'ac9_cloth_sim.place_tool');self.assertFalse(sp.show_gizmo_object_translate or sp.show_gizmo_object_rotate)
        WS._set_gizmo(sp,bpy.context.scene,'ac9_cloth_sim.transform_pattern_tool');self.assertTrue(sp.show_gizmo_object_translate or sp.show_gizmo_object_rotate)
    def test_save_load_keeps_basis_and_vertices(self):
        before=self.co();path=OUT.parent/'placement_basis.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(path));bpy.ops.wm.open_mainfile(filepath=str(path));ST.invalidate()
        self.g=CB.garment(bpy.context);PM._busy[0]=True
        self.assertEqual(CB.basis(self.g),{'A':CB.PLACED,'B':CB.PLACED});self.assertFalse(D.has_result(self.g));np.testing.assert_array_equal(before,self.co())
    def test_save_load_result_survives_cache_loss(self):
        CB.adopt(bpy.context,self.g);before=self.co();path=OUT.parent/'result_basis.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(path));bpy.ops.wm.open_mainfile(filepath=str(path));ST.invalidate()
        self.g=CB.garment(bpy.context);PM._busy[0]=True
        self.assertEqual(CB.basis(self.g),{'A':CB.RESULT,'B':CB.RESULT});self.assertTrue(D.has_result(self.g));np.testing.assert_array_equal(before,self.co())
    def test_save_load_unknown_stays_unknown(self):
        CB.publish(bpy.context,self.g,{});path=OUT.parent/'unknown_basis.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(path));bpy.ops.wm.open_mainfile(filepath=str(path));ST.invalidate()
        self.g=CB.garment(bpy.context);PM._busy[0]=True
        self.assertEqual(CB.basis(self.g),{'A':CB.UNKNOWN,'B':CB.UNKNOWN});self.assertFalse(D.has_result(self.g));self.assertIsNone(PM.ensure_handle(bpy.context,self.g,['A']))
    def test_no_frame_finish_restores_seed_and_basis(self):
        from types import SimpleNamespace
        before=self.co();CB.adopt(bpy.context,self.g);D.set_positions(self.g,(before+20)*.001)
        op=SimpleNamespace(info={'dir':str(OUT.parent/'no_frames'),'stages':['s1']},ob=self.g,prev_job=None,_pushed=False,
                           _basis_before={'A':CB.PLACED,'B':CB.PLACED},_mk=('',None,None,None,before,None))
        with self.assertRaises(D.NoFrame):D.AC9_OT_SimDress._finish(op,bpy.context)
        np.testing.assert_allclose(before,self.co(),atol=.00003);self.assertEqual(CB.basis(self.g),{'A':CB.PLACED,'B':CB.PLACED})
    def test_identity_delta_restores_shape_and_pose(self):
        before=self.co();sp=ST.text(bpy.context).as_string();h=PM.ensure_handle(bpy.context,self.g,['A']);M=h.matrix_world.copy();st=PM._state[h.name]
        h.matrix_world=Matrix.Translation((.01,0,0))@M;PM._apply(bpy.context,h,st)
        h.matrix_world=M;PM._apply(bpy.context,h,st)
        np.testing.assert_array_equal(before,self.co());self.assertEqual(S.loads(sp),ST.get(bpy.context)[0])
    def test_shifted_front_back_mirror(self):
        sp=ST.get(bpy.context)[0]
        for p in sp['pieces']:p['mirror_plane']='y'
        sp['guides']['torso']['o'][1]=20;ST.put(bpy.context,sp)
        before=self.co();self.move(T=Matrix.Translation((0,.01,0)));d=self.co()-before
        np.testing.assert_allclose(d[:3],[[0,10,0]]*3,atol=.00003);np.testing.assert_allclose(d[3:],[[0,-10,0]]*3,atol=.00003)
    def test_guide_removes_both_pose_offsets(self):
        self.move()
        SEL.apply(bpy.context,['A'],'SET',active='A',view='world')
        self.assertEqual(bpy.ops.ac9_cloth_sim.reset_placed('EXEC_DEFAULT'),{'FINISHED'})
        sp=ST.get(bpy.context)[0]
        self.assertTrue(all('pose' not in sp['placement'][n] for n in ['A','B']))
        self.assertEqual({c['basis'] for c in CB.contexts(bpy.context,sp).values()},{CB.PLACED})
        PL.sync(bpy.context)
        self.assertTrue(all('pose' not in ST.get(bpy.context)[0]['placement'][n] for n in ['A','B']))
    def test_next_move_does_not_restore_old_pair_or_other_settings(self):
        h,st=self.move();st['last']=tuple(map(tuple,h.matrix_world))
        sp=ST.get(bpy.context)[0];sp['pairs']=[];sp['unrelated_control_probe']='keep';ST.put(bpy.context,sp)
        before=self.co();h.matrix_world=Matrix.Translation((.01,0,0))@h.matrix_world;PM._apply(bpy.context,h,st)
        PL.sync(bpy.context);now=ST.get(bpy.context)[0]
        self.assertEqual(now['pairs'],[]);self.assertEqual(now['unrelated_control_probe'],'keep');np.testing.assert_array_equal((self.co()-before)[3:],0)
    def test_rebase_keeps_external_correction(self):
        CB.adopt(bpy.context,self.g);h,st=self.move();st['last']=tuple(map(tuple,h.matrix_world))
        new=self.co();new[:3,1]+=8;D.set_positions(self.g,new*.001);PM.rebase_handles()
        h.matrix_world=Matrix.Translation((.01,0,0))@h.matrix_world;PM._apply(bpy.context,h,st)
        np.testing.assert_allclose(self.co()[:3,1],8,atol=.00003)
    def test_unknown_fake_job_is_not_provenance(self):
        self.g.data.pop(CB.PROP);self.g.data.pop(CB.OWNER);self.g['ac9_job']='not_a_real_result'
        self.assertEqual(CB.basis(self.g),{'A':CB.UNKNOWN,'B':CB.UNKNOWN});self.assertIsNone(PM.ensure_handle(bpy.context,self.g,['A']))
    def test_partial_resolve_preserves_unselected_unknown(self):
        CB.publish(bpy.context,self.g,{});sp=ST.get(bpy.context)[0];sp['pairs']=[];ST.put(bpy.context,sp)
        before=self.co();SEL.apply(bpy.context,['A'],'SET',active='A',view='world')
        self.assertEqual(bpy.ops.ac9_cloth_sim.reset('EXEC_DEFAULT'),{'FINISHED'})
        self.assertIsNotNone(bpy.data.objects.get(self.g.name));np.testing.assert_array_equal(before[3:],self.co()[3:])
        cc=CB.contexts(bpy.context);self.assertEqual(cc['A']['basis'],CB.PLACED);self.assertEqual(cc['B']['basis'],CB.UNKNOWN)
    def test_old_run_cannot_write_same_size_replacement(self):
        from types import SimpleNamespace
        bind=(ST.text(bpy.context),self.g.session_uid,self.g.data.session_uid)
        op=SimpleNamespace(ob=self.g,ob_name=self.g.name,_control_binding=bind)
        self.g.data=self.g.data.copy();before=self.co()
        with self.assertRaises(ValueError):D.AC9_OT_SimDress._garment(op,bpy.context)
        np.testing.assert_array_equal(before,self.co())
    def test_start_releases_placed_mask_without_adopting_preview(self):
        # Exercise the real publication/drop boundary, before any solver frame.
        for basis in (CB.PLACED, CB.UNKNOWN, CB.RESULT):
            with self.subTest(basis=basis):
                coll=bpy.data.collections.get(PV.COLL_NAME)
                if coll is None:
                    coll=bpy.data.collections.new(PV.COLL_NAME)
                    bpy.context.scene.collection.children.link(coll)
                me=bpy.data.meshes.new('StartPlaced')
                me.from_pydata([(0,0,0),(.04,0,0),(0,0,.04)],[],[(0,1,2)])
                ob=bpy.data.objects.new('StartPlaced',me);coll.objects.link(ob)
                ob[PV.PROP]='A';ob[PV.GARMENT_PROP]=self.sp['name']
                self.g=D.garment_object(bpy.context,D.garment_name(self.sp),self.flat,self.F,self.fp,self.X,['A','B'],editing_basis={'A':basis,'B':CB.RESULT})
                before=CB.basis(self.g)
                PV.drop(self.sp)
                bpy.context.view_layer.update()
                evaluated=self.g.evaluated_get(bpy.context.evaluated_depsgraph_get())
                self.assertEqual(len(evaluated.data.polygons),2)
                self.assertEqual(CB.basis(self.g),before)
                self.assertNotIn('A',DS.hidden_result(self.sp,self.g))
                self.assertIsNone(self.g.modifiers.get(D.HIDE_MOD))
                from ac9_cloth_sim import piece_hide as PH
                for state in ('freeze','off_pattern','off_all'):
                    S.set_piece_state(self.sp['pieces'][1],state)
                    ST.put(bpy.context,self.sp)
                    PV.drop(self.sp);bpy.context.view_layer.update()
                    evaluated=self.g.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    self.assertEqual(len(evaluated.data.polygons),2 if state=='freeze' else 1)
                PH.set_hidden(bpy.context,self.sp,['A'])
                PV.drop(self.sp);bpy.context.view_layer.update()
                self.assertEqual(len(self.g.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons),0)
                PH.show_all(bpy.context,self.sp)
                S.set_piece_state(self.sp['pieces'][1],'active');ST.put(bpy.context,self.sp)

res=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ControlBasis))
OUT.write_text(json.dumps({'run':res.testsRun,'failures':[str(x[0])+'\n'+x[1] for x in res.failures],'errors':[str(x[0])+'\n'+x[1] for x in res.errors]},ensure_ascii=False,indent=2),encoding='utf8')
if not res.wasSuccessful():raise RuntimeError('Control basis contract failures')
