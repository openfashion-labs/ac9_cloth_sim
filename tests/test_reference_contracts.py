"""Saved reference lifetime: unchanged geometry, deletion, and reuse of names."""
import copy
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import draft as D, spec as S, elastic as EL, attach as AT, zippers as ZP, buttons as BT
from ac9_cloth_sim.core import dart as DT


def fixture():
    sp = S.new_spec('ReferenceContracts')
    for i, name in enumerate(('A', 'B', 'C')):
        D.add_polygon(sp, [[i*200,0],[i*200+100,0],[i*200+100,100],[i*200,100]], name=name)
    return sp


def side(piece='A', start=.6, end=.9, direction=1):
    return {'piece':piece, 'from':['E1',start], 'to':['E1',end], 'dir':direction}


def ends(sp, sd):
    return S.side_polyline(sp, sd)[[0,-1]]


class ReferenceContracts(unittest.TestCase):
    def test_outline_interval_split_and_join_round_trip(self):
        for fraction in (.25, .5, .75):
            for direction in (1,-1):
                for start,end in ((0,1),(.05,.2),(.1,.9),(.8,.95)):
                    with self.subTest(fraction=fraction,direction=direction,start=start,end=end):
                        sp=fixture();sd=side(start=start,end=end,direction=direction)
                        sp['elastic']=[dict(piece='A',ratio=.7,side=copy.deepcopy(sd),enabled=False)]
                        sp['attach']=[dict(id='held',side=copy.deepcopy(sd),tape='waist',enabled=False)]
                        before=ends(sp,sd)
                        D.split_edge(sp,'A','E1',fraction,twins=False)
                        for feature in ('elastic','attach'):
                            np.testing.assert_allclose(ends(sp,sp[feature][0]['side']),before,atol=.0001,rtol=0)
                        D.join_at_corner(sp,'A',1,twins=False)
                        for feature in ('elastic','attach'):
                            np.testing.assert_allclose(ends(sp,sp[feature][0]['side']),before,atol=.0001,rtol=0)
                        self.assertEqual(sp['attach'][0]['tape'],'waist')
                        self.assertEqual(AT.entries(sp),[])

    def test_elastic_outer_owner_is_authoritative(self):
        sp=fixture();sd=side();sd.pop('piece')
        sp['elastic']=[dict(piece='A',ratio=.7,side=sd)]
        before=np.asarray(EL.solver_entries(sp,['A'])[0]['line'])[[0,-1]]
        D.split_edge(sp,'B','E1',.5,twins=False)
        self.assertEqual(sd['from'],['E1',.6])
        D.split_edge(sp,'A','E1',.5,twins=False)
        after=np.asarray(EL.solver_entries(sp,['A'])[0]['line'])[[0,-1]]
        np.testing.assert_allclose(after,before,atol=.001,rtol=0)

    def test_curve_interval_follows_same_mapping_as_seam(self):
        sp=fixture();edge=D.piece(sp,'A')['edges'][0]
        edge['ctrl']=[[10,-30],[80,-20]];D.refresh_curve(edge)
        sd=side(start=.2,end=.8,direction=-1)
        sp['elastic']=[dict(piece='A',ratio=.7,side=copy.deepcopy(sd))]
        sp['seams']=[dict(id='S1',a=copy.deepcopy(sd),b=side('B'))]
        D.split_edge(sp,'A','E1',.4,twins=False)
        np.testing.assert_allclose(ends(sp,sp['elastic'][0]['side']),ends(sp,sp['seams'][0]['a']),atol=1e-8,rtol=0)

    def test_delete_retires_owned_relationships_but_preserves_other_domains(self):
        sp=fixture();D.piece(sp,'B')['family']='A';D.piece(sp,'B')['closes_onto']='A'
        sp['pairs']=[['A','B'],['B','C']];sp['closures']=[['A','B'],['B','C']]
        sp['elastic']=[dict(piece='A',ratio=.7),dict(piece='C',ratio=.9)]
        sp['measure']=dict(targets=dict(waist=720),pattern=[dict(name='gone',a=dict(piece='A',edge='E1',t=.2),b=dict(piece='B',edge='E1',t=.3)),dict(name='kept',a=dict(piece='B',edge='E1',t=.2),b=dict(piece='C',edge='E1',t=.3))])
        sp['seams']=[dict(id='gone',a=side('A'),b=side('B')),dict(id='kept',a=side('B'),b=side('C'))]
        sp['folds']=[dict(seam='gone',turn='A',deg=30),dict(seam='kept',turn='B',deg=45)]
        sp['attach']=[dict(id='gone_side',side=side('A'),tape='waist'),dict(id='gone_seam',seam='gone',tape='waist'),dict(id='kept',seam='kept',tape='waist')]
        kept_piece=copy.deepcopy(D.piece(sp,'C'));gone=D.delete_piece(sp,'A')
        self.assertEqual(gone,['gone']);self.assertEqual(D.piece(sp,'C'),kept_piece)
        self.assertEqual(D.piece(sp,'B')['family'],'A');self.assertNotIn('closes_onto',D.piece(sp,'B'))
        self.assertEqual(sp['pairs'],[['B','C']]);self.assertEqual(sp['closures'],[['B','C']])
        self.assertEqual(sp['elastic'],[dict(piece='C',ratio=.9)])
        self.assertEqual(sp['measure']['targets'],dict(waist=720));self.assertEqual([m['name'] for m in sp['measure']['pattern']],['kept'])
        self.assertEqual([a['id'] for a in sp['attach']],['kept']);self.assertEqual(sp['folds'],[dict(seam='kept',turn='B',deg=45)])
        D.add_polygon(sp,[[600,0],[700,0],[600,100]],name='A')
        self.assertEqual(EL.solver_entries(sp,['A']),[])
        self.assertNotIn(('A','B'),S.placement_pairs(sp))

    def test_internal_delete_is_core_owned_and_piece_scoped(self):
        sp=fixture()
        for name in ('A','B'):
            D.piece(sp,name)['internals']=[dict(id='L1',pts=[[10,10],[50,50]],kind='line',closed=False)]
        sp['seams']=[dict(id='gone',a=dict(piece='A',line='L1',**{'from':0,'to':1}),b=side('B'))]
        sp['folds']=[dict(seam='gone',turn='A',deg=30),dict(piece='A',line='L1',deg=40),dict(piece='B',line='L1',deg=50)]
        sp['elastic']=[dict(piece='A',line='L1',ratio=.7),dict(piece='A',ratio=.8),dict(piece='B',line='L1',ratio=.9)]
        sp['attach']=[dict(id='seam',seam='gone'),dict(id='line',side=dict(piece='A',line='L1')),dict(id='kept',side=dict(piece='B',line='L1'))]
        self.assertEqual(D.delete_internal(sp,'A','L1',mirror=False),['gone'])
        self.assertEqual(sp['folds'],[dict(piece='B',line='L1',deg=50)])
        self.assertEqual(sp['elastic'],[dict(piece='A',ratio=.8),dict(piece='B',line='L1',ratio=.9)])
        self.assertEqual([a['id'] for a in sp['attach']],['kept'])
        self.assertEqual(len(D.piece(sp,'B')['internals']),1)

    def test_no_optional_feature_is_created_by_unrelated_edit(self):
        sp=fixture();D.split_edge(sp,'A','E1',.5,twins=False);D.delete_piece(sp,'C')
        for key in ('elastic','attach','measure','pairs','closures'):self.assertNotIn(key,sp)

    def test_split_and_join_preserve_whole_zipper_side(self):
        sp=fixture();sp['zippers']=[dict(id='Z1',a=dict(piece='A',edges=['E1']),b=dict(piece='B',edges=['E1']),closed=.5)]
        before=ZP._chain(sp,'A',['E1'])
        D.split_edge(sp,'A','E1',.3,twins=False)
        ids=sp['zippers'][0]['a']['edges']
        self.assertEqual(len(ids),2)
        after=ZP._chain(sp,'A',ids)
        np.testing.assert_allclose(after[[0,-1]],before[[0,-1]],atol=1e-8,rtol=0)
        self.assertAlmostEqual(S.polyline_len(after),S.polyline_len(before))
        D.join_at_corner(sp,'A',1,twins=False)
        self.assertEqual(sp['zippers'][0]['a']['edges'],['E1'])

    def test_join_does_not_silently_extend_zipper(self):
        sp=fixture();sp['zippers']=[dict(id='Z1',a=dict(piece='A',edges=['E1']),b=dict(piece='B',edges=['E1']))]
        raw=S.dumps(sp)
        with self.assertRaises(ValueError):D.join_at_corner(sp,'A',1,twins=False)
        self.assertEqual(S.dumps(sp),raw)
        with self.assertRaises(ValueError):D._join_one(sp,D.piece(sp,'A'),1,keep_shape=True)
        self.assertEqual(S.dumps(sp),raw)

    def test_twin_zipper_refusal_does_not_change_main_piece(self):
        sp=fixture();twin=D.mirror_copy(sp,'A')
        sp['zippers']=[dict(id='Z1',a=dict(piece=twin,edges=['E1']),b=dict(piece='B',edges=['E1']))]
        raw=S.dumps(sp)
        with self.assertRaises(ValueError):D.join_at_corner(sp,'A',1,twins=True)
        self.assertEqual(S.dumps(sp),raw)

    def test_symmetric_internal_delete_removes_both_lines_settings(self):
        sp=fixture();D.piece(sp,'A')['mirror_edit']=True
        lid=D.add_internal(sp,'A',[[10,20],[20,60]],mirror=True)
        ids=[line['id'] for line in D.piece(sp,'A')['internals']]
        self.assertEqual(len(ids),2)
        sp['elastic']=[dict(piece='A',line=line,ratio=.7) for line in ids]
        D.delete_internal(sp,'A',lid)
        self.assertEqual(D.piece(sp,'A')['internals'],[])
        self.assertFalse(sp.get('elastic'))

    def test_deleted_fastener_targets_do_not_rebind_to_a_reused_name(self):
        sp=fixture()
        sp['buttons']=[dict(id='gone',piece='A',pt=[10,10],style='a',hole=None),
                       dict(id='kept',piece='B',pt=[220,20],style='b',hole=dict(piece='A',pt=[20,20]),hole_by='hand',fastened=True),
                       dict(id='unrelated',piece='C',pt=[420,20],style='c',hole=None)]
        sp['zippers']=[dict(id='gone',a=dict(piece='A',edges=['E1']),b=dict(piece='B',edges=['E1'])),
                       dict(id='kept',a=dict(piece='B',edges=['E2']),b=dict(piece='C',edges=['E2']))]
        sp['tacks']=[dict(a='A',b='B',pa=[10,10],pb=[220,20]),dict(a='B',b='C',pa=[230,20],pb=[420,20])]
        untouched=copy.deepcopy(sp['buttons'][2])
        D.delete_piece(sp,'A')
        self.assertEqual([b['id'] for b in sp['buttons']],['kept','unrelated'])
        self.assertEqual(sp['buttons'][1],untouched)
        self.assertIsNone(sp['buttons'][0]['hole']);self.assertFalse(sp['buttons'][0]['fastened'])
        self.assertEqual(sp['buttons'][0]['hole_by'],'hand')
        self.assertEqual([z['id'] for z in sp['zippers']],['kept'])
        self.assertEqual(len(sp['tacks']),1)
        D.add_polygon(sp,[[600,0],[700,0],[600,100]],name='A')
        self.assertEqual(BT.entries(sp,['A','B','C']),[])

    def test_repeated_preview_does_not_change_baseline_references(self):
        sp=fixture();sp['elastic']=[dict(piece='A',ratio=.7,side=side())]
        sp['attach']=[dict(id='held',side=side(),tape='waist')]
        raw=S.dumps(sp)
        for offset in (20,30):
            trial=D.edit_copy(sp,{'A'})
            D.move_corners(trial,'A',{1:(offset,0)},twins=False)
            self.assertEqual(S.dumps(sp),raw)
        direct=D.edit_copy(sp,{'A'});D.move_corners(direct,'A',{1:(30,0)},twins=False)
        self.assertEqual(S.dumps(trial),S.dumps(direct))

    def test_dart_search_trial_does_not_mutate_elastic_or_attach(self):
        sp=fixture();DT.add_dart(sp,'A','E1',.5,width=20,length=30,twins=False,true=False)
        edge=D.piece(sp,'A')['edges'][0]['id']
        sd=side();sd['from'][0]=sd['to'][0]=edge
        sp['elastic']=[dict(piece='A',ratio=.7,side=copy.deepcopy(sd))]
        sp['attach']=[dict(id='held',side=copy.deepcopy(sd),tape='waist')]
        dart=DT.find_darts(sp,'A')[0];raw=S.dumps(sp)
        trial=DT._with_mouth(sp,'A',dart,dart['length']+3)
        self.assertEqual(S.dumps(sp),raw)
        self.assertNotEqual(D.piece(trial,'A')['edges'],D.piece(sp,'A')['edges'])

    def test_match_side_length_uses_isolated_trial_side(self):
        sp=fixture()
        sp['seams']=[dict(id='S1',a=side(start=.2,end=1),b=side('B',start=0,end=1))]
        sp['elastic']=[dict(piece='A',ratio=.7,side=side(start=.3,end=.8))]
        held_before=ends(sp,sp['elastic'][0]['side'])
        changed,old,new,target=D.match_side_length(sp,'S1','a',end='to')
        self.assertIn('A',changed);self.assertAlmostEqual(old,80)
        self.assertAlmostEqual(new,target,places=3)
        # Interior elastic points keep distance from the unchanged end, once only.
        np.testing.assert_allclose(ends(sp,sp['elastic'][0]['side']),held_before,atol=.0001,rtol=0)


if __name__=='__main__':unittest.main()
