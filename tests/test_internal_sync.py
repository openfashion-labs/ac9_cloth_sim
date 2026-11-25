# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored mirror-line contracts; no simulation or graphical context needed."""
import copy,json,unittest
import numpy as np
from ac9_cloth_sim.core import draft as D,spec as S,iline as IL,fold_hand as FH,pattern_build as PB
try:
    from ac9_cloth_sim.core import internal_sync as IS
except ImportError:IS=None
KEY='mirror_id'


class InternalSync(unittest.TestCase):
    def fixture(self, before=False, plane='x', self_sym=False):
        sp=S.new_spec(self._testMethodName)
        D.add_polygon(sp,[[0,0],[200,0],[200,200],[0,200]],name='P')
        if self_sym:D.piece(sp,'P')['mirror_edit']=True
        lid=D.add_internal(sp,'P',[[60,0],[60,200]]) if before else None
        made,_=D.mirror_group(sp,['P'],plane=plane);q=made['P']
        B=S.Outline(D.piece(sp,'P')).B
        self.assertTrue(np.isfinite(B).all());self.assertAlmostEqual(abs(D.signed_area(B)),40000.)
        return sp,q,lid
    def line(self,sp,p,l):return D.internal(D.piece(sp,p),l)
    def assert_mirror(self,sp,p,l):
        q,ql=D.twin_line(sp,p,l);a,b=self.line(sp,p,l),self.line(sp,q,ql)
        self.assertEqual(a[KEY],b[KEY]);self.assertTrue(a[KEY])
        M=D.mirror_fn(D.piece(sp,p),D.piece(sp,q))
        self.assertLess(float(D._dist_to_polyline(M(a['pts']),np.asarray(b['pts'])).max()),.00011)
        self.assertEqual(FH.mapped_hand(a,b,M),FH.hand(b))
        return q,ql
    def test_add_after_copy_from_both_sides_and_planes(self):
        for plane in ('x','y'):
            for reverse in (False,True):
                sp,q,_=self.fixture(plane=plane);p=q if reverse else 'P'
                B=S.Outline(D.piece(sp,p)).B;lo=B.min(0);hi=B.max(0)
                lid=D.add_internal(sp,p,[[lo[0]+60,lo[1]],[lo[0]+60,hi[1]]])
                self.assertEqual([len(x['internals']) for x in sp['pieces']],[1,1]);self.assert_mirror(sp,p,lid)
    def test_copy_existing_lines_assigns_stable_ids(self):
        sp,q,l=self.fixture(before=True);self.assert_mirror(sp,'P',l)
    def test_move_known_pair_repairs_resampling_and_small_drift(self):
        for resample in (False,True):
            sp,q,l=self.fixture(before=True);a,b=self.line(sp,'P',l),self.line(sp,q,l)
            token=a[KEY];Q=np.array(b['pts']);b['pts']=(np.array([Q[0],Q.mean(0),Q[1]]) if resample else Q+[.2,0]).tolist()
            changed=D.move_internal(sp,'P',l,'all',[10,0]);self.assertEqual(set(changed),{'P',q})
            self.assertEqual(a[KEY],token);self.assert_mirror(sp,'P',l)
    def test_move_from_follower_and_endpoint_reversed(self):
        sp,q,l=self.fixture(before=True);b=self.line(sp,q,l);b['pts'].reverse()
        D.move_internal(sp,q,l,'end',[12,0]);self.assert_mirror(sp,q,l)
    def test_curve_nodes_and_handles_follow_stored_peer(self):
        sp,q,_=self.fixture();l=IL.add_shape(sp,'P',[[60,0],[70,100],[60,200]],
            [np.array([[20,30],[110,70]]),None],kind='line',closed=False)
        IL.move_node(sp,'P',l,1,[5,10]);self.assert_mirror(sp,'P',l)
        IL.set_handle(sp,'P',l,0,0,[30,40]);self.assert_mirror(sp,'P',l)
        self.assertTrue(self.line(sp,q,D.twin_line(sp,'P',l)[1]).get('ctrl'))
    def test_delete_mirrored_batch_and_dependencies(self):
        sp,q,l=self.fixture(before=True)
        sp['folds']=[dict(piece=n,line=l,deg=90.,side='A') for n in ('P',q)]
        sp['elastic']=[dict(piece=n,line=l,ratio=.7) for n in ('P',q)]
        side=lambda n:dict(piece=n,line=l,**{'from':0.,'to':1.})
        sp['seams']=[dict(id='S',a=side('P'),b=side(q),enabled=True)]
        sp['attach']=[dict(side=side(q))]
        unrelated=D.add_internal(sp,'P',[[100,0],[100,200]])
        D.delete_internal(sp,q,l)
        self.assertEqual([len(x['internals']) for x in sp['pieces']],[1,1])
        self.assertFalse(sp['seams']);self.assertFalse(sp['folds']);self.assertFalse(sp.get('elastic'));self.assertFalse(sp.get('attach'))
        self.assert_mirror(sp,'P',unrelated)
    def test_self_axis_and_off_axis_delete_once(self):
        for x,want in ((100,1),(60,2)):
            sp,q,_=self.fixture(self_sym=True)
            lid=D.add_internal(sp,'P',[[x,0],[x,200]])
            self.assertEqual([len(p['internals']) for p in sp['pieces']],[want,want])
            D.move_internal(sp,'P',lid,'all',[10,0]);self.assert_mirror(sp,'P',lid)
            D.delete_internal(sp,'P',lid);self.assertEqual([len(p['internals']) for p in sp['pieces']],[0,0])
    def test_closed_internal_shape_and_hole_sync(self):
        sp,q,_=self.fixture();l=D.add_internal(sp,'P',[[20,20],[50,20],[50,50],[20,50]],kind='shape',closed=True)
        self.assert_mirror(sp,'P',l);D.set_hole(sp,'P',l,True)
        self.assertTrue(self.line(sp,q,D.twin_line(sp,'P',l)[1])['hole'])
    def test_legacy_match_persists_only_on_edit(self):
        sp,q,l=self.fixture(before=True)
        for p in sp['pieces']:
            for it in p['internals']:it.pop(KEY,None)
        self.line(sp,q,l)['pts'].insert(1,np.mean(self.line(sp,q,l)['pts'],axis=0).tolist())
        before=S.dumps(sp);self.assertIsNotNone(D.twin_line(sp,'P',l));self.assertEqual(S.dumps(sp),before)
        D.move_internal(sp,'P',l,'all',[10,0]);self.assert_mirror(sp,'P',l)
    def test_legacy_different_traversal_never_matches_or_deletes_peer(self):
        sp,q,_=self.fixture();P=np.array([[0,0],[80,40],[80,120],[10,160],[200,200.]])
        M=D.mirror_fn(D.piece(sp,'P'),D.piece(sp,q))
        l=D.add_internal(sp,'P',P,mirror=False)
        r=D.add_internal(sp,q,M(P[[0,3,2,1,4]]),mirror=False)
        self.assertFalse(D.internal_problems(sp,'P'));self.assertFalse(D.internal_problems(sp,q))
        self.assertIsNone(D.twin_line(sp,'P',l));self.assertFalse(IS.compatible(sp,'P',q))
        D.delete_internal(sp,'P',l)
        self.assertEqual([it['id'] for it in D.piece(sp,q)['internals']],[r])
    def test_legacy_closed_cyclic_reversed_resampled_match(self):
        sp,q,_=self.fixture();P=np.array([[20,20],[100,20],[100,100],[20,100],[20,20.]])
        l=D.add_internal(sp,'P',P,kind='shape',closed=True,mirror=False)
        M=D.mirror_fn(D.piece(sp,'P'),D.piece(sp,q))
        Q=np.array([[60,20],[100,20],[100,100],[20,100],[20,20],[60,20.]])[::-1]
        r=D.add_internal(sp,q,M(Q),kind='shape',closed=True,mirror=False)
        self.assertEqual(D.twin_line(sp,'P',l),(q,r));self.assertTrue(IS.compatible(sp,'P',q))
    def test_ambiguous_or_broken_link_refuses_before_mutation(self):
        for mode in ('duplicate','one_sided','foreign','ambiguous'):
            sp,q,l=self.fixture(before=True);a,b=self.line(sp,'P',l),self.line(sp,q,l)
            if mode=='duplicate':D.piece(sp,q)['internals'].append(dict(b,id='Other'))
            if mode=='one_sided':b.pop(KEY,None)
            if mode=='foreign':b[KEY]='unrelated'
            if mode=='ambiguous':
                a.pop(KEY,None);b.pop(KEY,None);D.piece(sp,q)['internals'].append(dict(b,id='Other'))
            before=S.dumps(sp)
            with self.assertRaises(ValueError):D.move_internal(sp,'P',l,'all',[10,0])
            self.assertEqual(S.dumps(sp),before)
    def test_unlink_placement_keeps_pattern_sync(self):
        sp,q,_=self.fixture();sp['placement']={'P':{'unlinked':True}}
        l=D.add_internal(sp,'P',[[60,0],[60,200]]);self.assert_mirror(sp,'P',l)
    def test_inactive_partner_still_gets_authored_line(self):
        sp,q,_=self.fixture();D.piece(sp,q)['enabled']=False
        l=D.add_internal(sp,'P',[[60,0],[60,200]]);self.assert_mirror(sp,'P',l)
    def test_off_symmetry_and_manual_placement_pair_do_not_sync(self):
        sp,q,_=self.fixture();sp['symmetry']='off';D.add_internal(sp,'P',[[60,0],[60,200]])
        self.assertEqual(len(D.piece(sp,q)['internals']),0)
        for p in sp['pieces']:p.pop('family',None)
        sp['symmetry']='place';sp['pairs']=[['P',q]];D.add_internal(sp,'P',[[80,0],[80,200]])
        self.assertEqual(len(D.piece(sp,q)['internals']),0)
    def test_duplicate_new_pair_never_reuses_ids(self):
        sp,q,l=self.fixture(before=True);old=self.line(sp,'P',l)[KEY]
        made,_=D.duplicate_group(sp,['P']);p=made['P']
        self.assertNotIn(KEY,self.line(sp,p,l))
        made,_=D.mirror_group(sp,[p]);self.assert_mirror(sp,p,l)
        self.assertNotEqual(old,self.line(sp,p,l)[KEY]);self.assertEqual(old,self.line(sp,'P',l)[KEY])
    def test_cancel_edit_snapshot_never_writes_partner(self):
        sp,q,l=self.fixture(before=True);before=S.dumps(sp)
        preview=D.edit_copy(sp,['P']);D.move_internal(preview,'P',l,'all',[10,0])
        self.assertEqual(S.dumps(sp),before);self.assert_mirror(preview,'P',l)
        self.assertNotEqual(preview['pieces'],sp['pieces'])
    def test_json_roundtrip_keeps_line_identity(self):
        sp,q,l=self.fixture(before=True);token=self.line(sp,'P',l)[KEY]
        sp=S.loads(S.dumps(sp));D.move_internal(sp,q,l,'start',[7,0]);self.assert_mirror(sp,q,l)
        self.assertEqual(self.line(sp,'P',l)[KEY],token)
    def test_line_group_rotation_scale_shift_uses_one_source(self):
        for both in (False,True):
            sp,q,l=self.fixture(before=True);lines=[('P',l)]+([(q,l)] if both else [])
            D.transform_group(sp,lines=lines,deg=15.,k=(1.1,.9),shift=(7,3))
            self.assert_mirror(sp,'P',l)
    def test_fold_and_elastic_use_the_same_correspondence(self):
        sp,q,l=self.fixture(before=True)
        sp['folds']=[dict(piece='P',line=l,deg=90.,side='B')];sp=D.carry_fold(sp,'P',l)
        self.assertEqual(len(sp['folds']),2);self.assertEqual({f['side'] for f in sp['folds']},{'B'})
        IS.set_elastic(sp,q,l,.7);self.assertEqual(len(sp['elastic']),2)
        self.assertTrue(IS.compatible(sp,'P',q))
        IS.set_elastic(sp,'P',l,1.);self.assertFalse(sp.get('elastic'))
    def test_missing_or_different_authored_line_is_not_shared(self):
        for mode in ('missing','geometry','fold','elastic'):
            sp,q,l=self.fixture(before=True)
            if mode=='missing':D.piece(sp,q)['internals']=[];self.line(sp,'P',l).pop(KEY,None)
            if mode=='geometry':self.line(sp,q,l)['pts'][0][0]+=.2
            if mode=='fold':sp['folds']=[dict(piece='P',line=l,deg=90.,side='A')]
            if mode=='elastic':sp['elastic']=[dict(piece='P',line=l,ratio=.7)]
            before=S.dumps(sp);pat,_=PB.build(sp,12.)
            self.assertTrue(all(int(i)<0 for i in pat['twin_src']),mode)
            self.assertEqual(S.dumps(sp),before)
    def test_small_fold_difference_invalidates_shared_mesh_and_shape_cache(self):
        from ac9_cloth_sim import dress
        sp,q,l=self.fixture(before=True)
        initial=(dress._build_key(sp,12.),dress._shapes_spec_key(sp))
        sp['folds']=[dict(piece='P',line=l,deg=35.,side='A')]
        self.assertNotEqual(dress._build_key(sp,12.),initial[0])
        self.assertNotEqual(dress._shapes_spec_key(sp),initial[1])
        sp=D.carry_fold(sp,'P',l)
        self.assertEqual(dress._build_key(sp,12.),initial[0])
        self.assertEqual(dress._shapes_spec_key(sp),initial[1])
    def test_rna_rejects_broken_correspondence_without_partial_settings(self):
        import bpy
        from ac9_cloth_sim import store as ST,draft_tool as DT,place_fold as PF
        sp,q,l=self.fixture(before=True);self.line(sp,q,l).pop(KEY,None)
        sp['folds']=[dict(piece=n,line=l,deg=90.,side='NONE') for n in ('P',q)]
        ST.new_text(bpy.context,sp['name']);ST.write(bpy.context,sp,keep=True)
        DT.select_internal(bpy.context,'P',l);before=ST.text(bpy.context).as_string()
        bpy.context.scene.ac9_iline_fold_angle=35.
        self.assertEqual(ST.text(bpy.context).as_string(),before)
        self.assertIn('Cannot match internal line',PF.why_not('P',l))
        bpy.context.scene.ac9_iline_elastic=70.
        self.assertEqual(ST.text(bpy.context).as_string(),before)
