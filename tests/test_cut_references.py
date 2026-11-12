"""Cut preserves feature geometry and publishes only complete plans."""
import copy
import unittest
import numpy as np
from ac9_cloth_sim.core import draft as D, spec as S, cut as C, elastic as EL


def fixture():
    sp = S.new_spec('CutReferences')
    for i, name in enumerate(('A', 'B')):
        D.add_polygon(sp, [[200*i,0],[200*i+100,0],[200*i+100,100],[200*i,100]], name=name)
    sp['placement'] = {'A':dict(guide='torso',theta=90,h=0,anchor=[25,50])}
    line = D.add_internal(sp, 'A', [[50,0],[50,100]], mirror=False)
    return sp, line


def side(a, b, direction=1):
    return dict(piece='A', **{'from':['E1',a], 'to':['E1',b], 'dir':direction})


class CutReferences(unittest.TestCase):
    def test_contained_intervals_keep_points_and_owner(self):
        for a,b,direction in ((.1,.4,1),(.6,.9,1),(.9,.6,-1),(.4,.1,-1)):
            with self.subTest(a=a,b=b,direction=direction):
                sp,line=fixture();sd=side(a,b,direction)
                sp['elastic']=[dict(piece='A',ratio=.7,side=copy.deepcopy(sd))]
                sp['attach']=[dict(id='held',side=copy.deepcopy(sd),enabled=False,tape='waist')]
                before=S.side_polyline(sp,sd)[[0,-1]]
                C.cut(sp,'A',line,twins=False)
                owner='A_2' if min(a,b)>.5 else 'A'
                self.assertEqual(sp['elastic'][0]['piece'],owner)
                for key in ('elastic','attach'):
                    target=sp[key][0]['side']
                    self.assertEqual(target['piece'],owner)
                    np.testing.assert_allclose(S.side_polyline(sp,target)[[0,-1]],before,atol=.0001,rtol=0)
                notes=[];self.assertEqual(len(EL.solver_entries(sp,['A','A_2'],notes)),1)
                self.assertEqual(notes,[])

    def test_crossing_interval_refuses_without_mutation(self):
        for enabled in (True,False):
            sp,line=fixture();D.internal(D.piece(sp,'A'),line)['pts']=[[50,0],[80,10],[80,90],[50,100]]
            sp['elastic']=[dict(piece='A',side=side(.2,.8),ratio=.7,enabled=enabled)]
            before=copy.deepcopy(sp)
            with self.assertRaisesRegex(ValueError,'interval crosses'):C.cut(sp,'A',line,twins=False)
            self.assertEqual(sp,before)

    def test_late_geometry_refusal_is_atomic(self):
        sp,line=fixture();D.add_internal(sp,'A',[[20,40],[30,40],[70,40],[80,40]],mirror=False)
        before=copy.deepcopy(sp)
        with self.assertRaisesRegex(ValueError,'crosses'):C.cut(sp,'A',line,twins=False)
        self.assertEqual(sp,before)

    def test_normal_cut_partitions_straight_band_without_loss(self):
        for a,b,direction in ((.2,.8,1),(.8,.2,-1)):
            sp,line=fixture();sd=side(a,b,direction)
            sp['elastic']=[dict(piece='A',side=sd,ratio=.7,w=10,strength=.3,enabled=True)]
            before=S.side_polyline(sp,sd)
            C.cut(sp,'A',line,twins=False)
            entries=EL.solver_entries(sp,['A','A_2'])
            self.assertEqual(len(entries),2)
            self.assertAlmostEqual(sum(S.polyline_len(e['line']) for e in entries),S.polyline_len(before),places=4)
            self.assertEqual({e['piece'] for e in entries},{'A','A_2'})
            for e in entries:
                self.assertEqual((e['ratio'],e['w'],e['ke_scale']),(.7,10,.3))
        for tilted in (False,True):
            sp,line=fixture();sp['elastic']=[dict(piece='A',side=side(.2,.8),ratio=.7)]
            if tilted:
                rotation=np.array([[1.,-1.],[1.,1.]])/np.sqrt(2)
                for edge in D.piece(sp,'A')['edges']:
                    edge['pts']=(np.asarray(edge['pts'])@rotation.T).tolist()
                it=D.internal(D.piece(sp,'A'),line);it['pts']=(np.asarray(it['pts'])@rotation.T).tolist()
                sp['placement']['A']['anchor']=(np.array([25.,50.])@rotation.T).tolist()
            else:
                D.internal(D.piece(sp,'A'),line)['pts']=[[50.0004,0],[50.0004,100]]
            before=S.dumps(sp)
            with self.assertRaises(ValueError):C.cut(sp,'A',line,twins=False)
            self.assertEqual(S.dumps(sp),before)

    def test_zipper_contained_and_crossing(self):
        for edge in ('E2','E1'):
            sp,line=fixture()
            sp['zippers']=[dict(id='Z1',a=dict(piece='A',edges=[edge]),b=dict(piece='B',edges=['E2']),closed=.5,pitch=5)]
            before=copy.deepcopy(sp)
            if edge=='E1':
                with self.assertRaisesRegex(ValueError,'zipper crosses'):C.cut(sp,'A',line,twins=False)
                self.assertEqual(sp,before)
            else:
                C.cut(sp,'A',line,twins=False)
                self.assertEqual(sp['zippers'][0]['a'],dict(piece='A_2',edges=['E2']))
                self.assertEqual(sp['zippers'][0]['b'],before['zippers'][0]['b'])

    def test_cartesian_features_follow_halves_without_moving(self):
        sp,line=fixture()
        sp['buttons']=[dict(id='B1',piece='A',pt=[80,30],hole=dict(piece='A',pt=[20,30]),fastened=True,style='gold',hole_by='hand')]
        sp['tacks']=[dict(a='A',pa=[80,20],b='A',pb=[50,20])]
        C.cut(sp,'A',line,twins=False)
        button=sp['buttons'][0]
        self.assertEqual(button,dict(id='B1',piece='A_2',pt=[80,30],hole=dict(piece='A',pt=[20,30]),fastened=True,style='gold',hole_by='hand'))
        self.assertEqual(sp['tacks'],[dict(a='A_2',pa=[80,20],b='A',pb=[50,20])])

    def test_cut_line_setting_refuses_atomically(self):
        sp,line=fixture();sp['elastic']=[dict(piece='A',line=line,ratio=.7)]
        before=copy.deepcopy(sp)
        with self.assertRaises(ValueError):C.cut(sp,'A',line,twins=False)
        self.assertEqual(sp,before)

    def test_cartesian_point_beside_cut_is_not_rounded_to_wrong_half(self):
        sp,line=fixture();sp['buttons']=[dict(id='B1',piece='A',pt=[50.25,20])]
        C.cut(sp,'A',line,twins=False)
        self.assertEqual(sp['buttons'][0]['piece'],'A_2')
        self.assertEqual(sp['buttons'][0]['pt'],[50.25,20])

    def test_contained_center_with_crossing_end_cap_refuses_atomically(self):
        for enabled in (True,False):
            sp,line=fixture();sp['elastic']=[dict(piece='A',side=side(.2,.4),ratio=.7,w=20,enabled=enabled)]
            before=S.dumps(sp)
            with self.assertRaisesRegex(ValueError,'band reaches'):C.cut(sp,'A',line,twins=False)
            self.assertEqual(S.dumps(sp),before)

    def test_internal_band_clearance_includes_ends_and_disabled_records(self):
        for enabled in (True,False):
            for as_side in (True,False):
                sp,line=fixture();lid=D.add_internal(sp,'A',[[30,10],[40,20]],mirror=False)
                entry=dict(piece='A',ratio=.7,w=20,enabled=enabled)
                entry.update(dict(side=dict(piece='A',line=lid,**{'from':0.,'to':1.})) if as_side else dict(line=lid))
                sp['elastic']=[entry];before=S.dumps(sp)
                with self.assertRaisesRegex(ValueError,'band reaches'):C.cut(sp,'A',line,twins=False)
                self.assertEqual(S.dumps(sp),before)

    def test_shared_baseline_retains_the_full_band_on_both_halves(self):
        sp,line=fixture();lid=D.add_internal(sp,'A',[[20,20],[30,20],[70,20],[80,20]],mirror=False)
        D.internal(D.piece(sp,'A'),lid)['kind']='baseline'
        sp['elastic']=[dict(piece='A',line=lid,ratio=.7,w=20)]
        before=EL.solver_entries(sp,['A'])[0]
        C.cut(sp,'A',line,twins=False)
        entries=EL.solver_entries(sp,['A','A_2']);self.assertEqual(len(entries),2)
        for entry in entries:
            self.assertEqual(dict(entry,piece='A'),before)

    def test_unrelated_geometry_is_shared_and_unchanged(self):
        sp,line=fixture();other=D.piece(sp,'B');before=copy.deepcopy(other)
        C.cut(sp,'A',line,twins=False)
        self.assertIs(D.piece(sp,'B'),other);self.assertEqual(other,before)

    def test_twin_failure_does_not_publish_successful_first_half(self):
        sp,line=fixture();tw=D.mirror_copy(sp,'A')
        mirror=D.mirror_fn(D.piece(sp,'A'),D.piece(sp,tw))
        D.add_internal(sp,tw,mirror([[20,40],[30,40],[70,40],[80,40]]).tolist(),mirror=False)
        before=S.dumps(sp)
        with self.assertRaisesRegex(ValueError,'crosses'):C.cut(sp,'A',line)
        self.assertEqual(S.dumps(sp),before)

    def test_internal_feature_owner_follows_moved_line(self):
        sp,line=fixture();lid=D.add_internal(sp,'A',[[80,20],[80,80]],mirror=False)
        sp['elastic']=[dict(piece='A',side=dict(piece='A',line=lid,**{'from':0,'to':1}),ratio=.7)]
        sp['attach']=[dict(id='held',side=dict(piece='A',line=lid,**{'from':0,'to':1}),enabled=False)]
        C.cut(sp,'A',line,twins=False)
        self.assertEqual(sp['elastic'][0]['piece'],'A_2')
        self.assertEqual(sp['elastic'][0]['side']['piece'],'A_2')
        self.assertEqual(sp['attach'][0]['side']['piece'],'A_2')
        self.assertEqual(len(EL.solver_entries(sp,['A_2'])),1)

    def test_duplicate_keeps_approved_group_relationship_scope(self):
        sp,line=fixture();D.add_polygon(sp,[[400,0],[500,0],[500,100],[400,100]],name='C')
        sp['seams']=[dict(id='inside',a=side(.1,.4),b=dict(side(.1,.4),piece='B')),
                     dict(id='outside',a=side(.6,.9),b=dict(side(.1,.4),piece='C'))]
        sp['folds']=[dict(piece='A',line=line,deg=20),dict(seam='inside',turn='B',deg=30),dict(seam='outside',turn='A',deg=10)]
        sp['elastic']=[dict(piece='A',ratio=.7)]
        sp['buttons']=[dict(id='B1',piece='A',pt=[20,20])]
        original=copy.deepcopy(sp)
        made,seams=D.duplicate_group(sp,['B','A','A'])
        self.assertEqual(len(made),2);self.assertEqual(len(seams),1)
        new=next(s for s in sp['seams'] if s['id']==seams[0])
        self.assertEqual((new['a']['piece'],new['b']['piece']),(made['A'],made['B']))
        self.assertEqual([f for f in sp['folds'] if f.get('seam')==seams[0]],[dict(seam=seams[0],turn=made['B'],deg=30)])
        self.assertEqual(sp['elastic'],original['elastic']);self.assertEqual(sp['buttons'],original['buttons'])
        self.assertTrue(all(n not in sp['placement'] for n in made.values()))
        self.assertEqual(sp['pieces'][:3],original['pieces'])

    def test_disabled_split_stays_disabled_and_twin_still_resolves(self):
        for enabled in (False,True):
            sp,line=fixture();tw=D.mirror_copy(sp,'A')
            sp['elastic']=[dict(piece='A',side=side(.2,.8),ratio=.7,enabled=enabled)]
            C.cut(sp,'A',line)
            self.assertEqual([e['enabled'] for e in sp['elastic']],[enabled,enabled])
            notes=[];entries=EL.solver_entries(sp,[p['name'] for p in sp['pieces']],notes)
            self.assertEqual(len(entries),4 if enabled else 0)
            self.assertEqual(notes,[])

    def test_split_seam_dependents_follow_each_physical_part(self):
        sp,line=fixture()
        sp['seams']=[dict(id='S1',a=side(0,1),b=dict(side(0,1),piece='B'))]
        sp['folds']=[dict(seam='S1',turn='A',deg=45,strength=3)]
        sp['attach']=[dict(id='held',seam='S1',tape='waist',enabled=False)]
        C.cut(sp,'A',line,twins=False)
        related=[sm for sm in sp['seams'] if sm['b']['piece']=='B']
        self.assertEqual(len(related),2)
        self.assertEqual(len(sp['folds']),2);self.assertEqual(len(sp['attach']),2)
        self.assertEqual(len({row['id'] for row in sp['attach']}),2)
        for sm in related:
            fold=next(row for row in sp['folds'] if row['seam']==sm['id'])
            self.assertEqual(fold['turn'],sm['a']['piece'])
            self.assertEqual((fold['deg'],fold['strength']),(45,3))
            held=next(row for row in sp['attach'] if row['seam']==sm['id'])
            self.assertFalse(held['enabled']);self.assertEqual(held['tape'],'waist')


if __name__=='__main__':unittest.main()
