# SPDX-License-Identifier: GPL-3.0-or-later
"""Public unplace lifecycle on finite, nondegenerate geometry; no solver quality claim."""
import bpy, sys, json, copy, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
args=sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,args[0])
import ac9_cloth_sim as A
from ac9_cloth_sim import store as ST, dress as D, placed_view as PV, placement as PL
from ac9_cloth_sim import display_state as DS, control_basis as CB, selection as SEL, pattern_view as FV
from ac9_cloth_sim import overlay as O, piece_move as PM
from ac9_cloth_sim import draft_tool as DT
from ac9_cloth_sim.core import spec as S, draft as DR
A.register()

class Unplace(unittest.TestCase):
    def test_button_name_uses_shared_definition(self):
        self.assertEqual(PL.AC9_OT_SimUnplace.bl_label,S.UNPLACE_NAME)
    def setUp(self):
        PM.clear();PM._busy[0]=True
        for ob in list(bpy.data.objects):bpy.data.objects.remove(ob,do_unlink=True)
        self.sp=S.new_spec('UnplaceTest')
        self.flat=[];self.tris=[];self.X=[]
        for i,n in enumerate(('P','Q','R')):
            x=i*100
            pts=np.array([[x,0],[x+40,0],[x+40,40],[x,40]],float)
            DR.add_polygon(self.sp,pts.tolist(),name=n)
            DR.add_internal(self.sp,n,[[x,20],[x+40,20]])
            self.sp['placement'][n]={'guide':S.FLAT,'flat':{'o':[x,0,100],'R':np.eye(3).tolist()},'anchor':[x,0]}
            self.flat.append(pts);self.tris.append(np.array([[0,1,2],[0,2,3]]));self.X.append(np.c_[pts,np.full(4,100.)])
        self.assertEqual(DR.problems(self.sp,['P','Q','R']),[])
        self.sp['folds']=[{'piece':'P','line':'I1','deg':90,'side':'A'}]
        self.sp['seams']=[{'id':'S1','a':{'piece':'P','from':['E2',0.],'to':['E2',1.],'dir':1},'b':{'piece':'R','from':['E4',0.],'to':['E4',1.],'dir':1}}]
        ST.new_text(bpy.context,self.sp['name']);ST.write(bpy.context,self.sp,keep=True)
        FV.rebuild(bpy.context,*ST.get(bpy.context))
        self.pat={'names':np.array(['P','Q','R']),**{'V'+str(i):v for i,v in enumerate(self.flat)}}
        self.g=D.garment_object(bpy.context,D.garment_name(self.sp),np.vstack(self.flat),np.vstack([t+i*4 for i,t in enumerate(self.tris)]),np.repeat(np.arange(3),2),np.vstack(self.X),['P','Q','R'],editing_basis={n:CB.RESULT for n in ('P','Q','R')})
    def tearDown(self):PM.clear();PM._busy[0]=False
    def select(self,*names):
        SEL.deselect_all(bpy.context)
        for n in names:FV.piece_objects()[n].select_set(True)
        bpy.context.view_layer.objects.active=FV.piece_objects()[names[0]] if names else None
    def spec(self):return ST.get(bpy.context)[0]
    def execute(self,*names):
        self.select(*names)
        return bpy.ops.ac9_cloth_sim.unplace('EXEC_DEFAULT')
    def co(self):return np.asarray([v.co[:] for v in self.g.data.vertices])
    def twin(self,unlinked=False):
        self.sp['pieces'][0]['family']='pair';self.sp['pieces'][1].update(family='pair',mirrored=True)
        if unlinked:
            for n in ('P','Q'):self.sp['placement'][n]['unlinked']=True
        ST.put(bpy.context,self.sp)
    def test_selected_only_preserves_definition_and_other_mesh(self):
        before=copy.deepcopy(self.sp);co=self.co().copy();bs=CB.basis(self.g)
        self.assertEqual(self.execute('P'),{'FINISHED'})
        after=self.spec();expected=copy.deepcopy(before);expected['placement'].pop('P')
        self.assertEqual(after,expected);np.testing.assert_array_equal(co,self.co())
        self.assertEqual(DS.snapshot(after)['P']['source'],'none')
        self.assertEqual(CB.basis(self.g)['Q'],bs['Q'])
        self.assertEqual(DS.snapshot(after)['R']['source'],'result')
    def test_linked_pair_both(self):
        self.twin();self.execute('Q');self.assertEqual(set(self.spec()['placement']),{'R'})
    def test_manual_pair_both(self):
        self.sp['pairs']=[['P','Q']];ST.put(bpy.context,self.sp)
        self.execute('P');self.assertEqual(set(self.spec()['placement']),{'R'})
    def test_off_partner_remains_linked(self):
        self.twin();S.set_piece_state(self.sp['pieces'][1],'off_all');ST.put(bpy.context,self.sp)
        self.execute('P');self.assertEqual(set(self.spec()['placement']),{'R'})
    def test_symmetry_off_preserves_old_unlinked_flags(self):
        self.twin(True);self.sp['symmetry']='off';ST.put(bpy.context,self.sp)
        self.execute('P','Q');sp=copy.deepcopy(self.spec());sp['symmetry']='on'
        self.assertTrue(S.pair_unlinked(sp,'P','Q'));self.assertFalse(S.linked_twins(sp))
    def test_unlinked_sequential_then_replacement_stays_independent(self):
        self.twin(True);self.execute('P');self.assertIn('Q',self.spec()['placement'])
        self.execute('Q');sp=copy.deepcopy(self.spec())
        self.assertFalse(S.linked_twins(sp));self.assertTrue(S.pair_unlinked(sp,'P','Q'))
        sp['placement']['P']=self.sp['placement']['P'];pl,_=S.mirror_twins(sp)
        self.assertNotIn('Q',pl)
        ST.put(bpy.context,sp);self.select('P');bpy.ops.ac9_cloth_sim.unlink_pair('EXEC_DEFAULT')
        self.assertFalse(S.pair_unlinked(self.spec(),'P','Q'))
    def test_central_symmetry_is_one_piece(self):
        self.sp['pieces'][0].update(mirror_edit=True,mirror_axis=20.)
        ST.put(bpy.context,self.sp);self.execute('P')
        self.assertEqual(set(self.spec()['placement']),{'Q','R'})
        self.assertTrue(self.spec()['pieces'][0]['mirror_edit'])
    def test_shared_mirror_object_pair_removed(self):
        self.twin();PV.build(bpy.context,self.sp,self.pat,self.tris,self.X,[(0,1)])
        self.assertIs(PV.objects(self.sp)['P'],PV.objects(self.sp)['Q'])
        self.execute('Q');self.assertFalse(set(PV.objects(self.spec())) & {'P','Q'})
        self.assertIn('R',PV.objects(self.spec()))
    def test_kept_hand_edit_and_before_seam_removed(self):
        PV.build(bpy.context,self.sp,self.pat,self.tris,self.X,[])
        ob=PV.objects(self.sp)['P'];ob.data.vertices[0].co.z+=.01
        PV.save_before_seam(ob,self.sp)
        self.assertIn('P',PV.before_seam(self.sp))
        PV.drop(self.sp)
        self.assertIn('P',PV._kept(self.sp));self.execute('P')
        self.assertNotIn('P',PV._kept(self.spec()));self.assertNotIn('P',PV.before_seam(self.spec()))
    def test_no_selection_is_noop(self):
        self.assertEqual(self.execute(),{'CANCELLED'});self.assertEqual(self.spec(),self.sp)
    def test_failure_restores_placement_mesh_basis(self):
        PV.build(bpy.context,self.sp,self.pat,self.tris,self.X,[])
        co=self.co().copy();basis=CB.basis(self.g);self.select('P')
        with patch.object(D,'set_shown_as_placed',side_effect=ValueError('injected refusal')):
            with self.assertRaisesRegex(RuntimeError,'injected refusal'):bpy.ops.ac9_cloth_sim.unplace('EXEC_DEFAULT')
        self.assertEqual(self.spec(),self.sp);np.testing.assert_array_equal(co,self.co())
        self.assertEqual(CB.basis(self.g),basis);self.assertIn('P',PV.objects(self.spec()))
    def test_invalid_membership_refuses_before_change(self):
        self.g.data.attributes.remove(self.g.data.attributes['ac9_piece'])
        self.select('P')
        with self.assertRaisesRegex(RuntimeError,'mapping is invalid'):bpy.ops.ac9_cloth_sim.unplace('EXEC_DEFAULT')
        self.assertEqual(self.spec(),self.sp)
    def test_one_invalid_face_refuses_without_hiding_other_faces(self):
        self.g.data.attributes['ac9_piece'].data[0].value=97
        self.select('P')
        with self.assertRaisesRegex(RuntimeError,'mapping is invalid'):bpy.ops.ac9_cloth_sim.unplace('EXEC_DEFAULT')
        self.assertEqual(self.spec(),self.sp)
        self.assertEqual(len(self.g.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons),6)
    def test_lines_selected_lines_notches_follow_actual_placed_owner(self):
        from ac9_cloth_sim.core import walk as W
        DR.add_notch(self.sp,'P','E2',.5,twins=False)
        DR.add_notch(self.sp,'R','E4',.5,twins=False)
        ST.put(bpy.context,self.sp)
        PV.build(bpy.context,self.sp,self.pat,self.tris,self.X,[],only={'P'})
        CB.publish(bpy.context,self.g,{n:CB.PLACED for n in ('P','Q','R')})
        DS.apply(bpy.context,self.sp);bpy.context.view_layer.update()
        self.assertFalse(D.has_result(self.g))
        ob=PV.objects(self.sp)['P'];ob.matrix_world.translation.z+=.05
        a=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');r=next(r for r in a.regions if r.type=='WINDOW')
        with bpy.context.temp_override(area=a,region=r):
            sp,ols=ST.get(bpy.context);O.garment_seams(bpy.context,sp)
            sh,_=PV.shown(bpy.context,sp,ols)
            Q=O._dense(sp['pieces'][0]['internals'][0]['pts']);expected=PV.to_world(sh['P'],Q)
            np.testing.assert_allclose(O._lines_to_3d(bpy.context,sp,ols,'P',[Q],True,sh,{})[0],expected,atol=1e-9)
            DT._isel.update(piece='P',line='I1');DT._imore.clear()
            np.testing.assert_allclose(O.ilines_picked3d(bpy.context,sp,ols,True)[0],expected,atol=1e-9)
            nts=O.notches3d(bpy.context,sp,ols,sp['seams'][0],True)
            ol=ols['P'];sd=sp['seams'][0]['a']
            material=np.asarray([ol.at(np.array([W.notch_s(ol,n)])).reshape(2) for _,n in W.side_notches(sp,sd,ols)])
            expected_notch=PV.to_world(sh['P'],material)
            np.testing.assert_allclose(nts[:len(material)],expected_notch,atol=1e-9)
            self.assertEqual(len(nts),2)
            O.ilines3d(bpy.context,sp,ols,True)
            self.execute('P');sp,ols=ST.get(bpy.context);O.garment_seams(bpy.context,sp)
            self.assertEqual(O.ilines_picked3d(bpy.context,sp,ols,True),[])
            self.assertEqual(len(O.notches3d(bpy.context,sp,ols,sp['seams'][0],True)),1)
    def test_saved_preview_recovery_does_not_adopt_and_is_unique(self):
        CB.publish(bpy.context,self.g,{n:CB.PLACED for n in ('P','Q','R')})
        before=self.co().copy();basis=CB.basis(self.g)
        with patch.object(D.time,'strftime',return_value='same_second'):
            first=D.job_or_from_mesh(bpy.context,self.g)
            self.g.pop('ac9_job',None)
            second=D.job_or_from_mesh(bpy.context,self.g)
        self.assertNotEqual(first,second)
        self.assertTrue(Path(first).is_dir() and Path(second).is_dir())
        self.assertEqual(CB.basis(self.g),basis);np.testing.assert_array_equal(before,self.co())
        self.execute('P','Q','R')
        self.g.pop('ac9_job',None)
        self.g[D.JOB_MISSING_PROP]=first
        with patch.object(D,'job_from_mesh',side_effect=AssertionError('must not restore unplaced')):
            self.assertIsNone(D.job_or_from_mesh(bpy.context,self.g))
    def test_missing_job_with_renamed_only_uv_recovers(self):
        before=self.co().copy();basis=CB.basis(self.g)
        self.g.data.uv_layers[0].name='RenamedPattern'
        self.g[D.JOB_MISSING_PROP]='missing-result-history'
        restored=D.job_or_from_mesh(bpy.context,self.g)
        self.assertTrue(restored and Path(restored).is_dir())
        self.assertEqual(CB.basis(self.g),basis);np.testing.assert_array_equal(before,self.co())

result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Unplace))
Path(args[1]).write_text(json.dumps({'run':result.testsRun,'health':'three finite 40mm squares, 1600mm2 each; ownership/geometry only, no physical run','failures':[tb for _,tb in result.failures],'errors':[tb for _,tb in result.errors]},indent=2),encoding='utf8')
if not result.wasSuccessful():raise RuntimeError('Unplace contract failures')
