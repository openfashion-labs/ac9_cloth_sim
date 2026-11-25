"""Dart cuts split an existing sewn interval; its retained ID is no longer the old whole seam.
Run in Blender: --python this.py -- <package parent> <out.json>
"""
import sys,json,copy,unittest
from pathlib import Path
args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
sys.path.insert(0,args[0] if args else str(Path(__file__).resolve().parents[2]))
import numpy as np
from ac9_cloth_sim.core import spec as S,draft as D,dart as DA,transfer as TR,pattern_build as PB
from ac9_cloth_sim import dress as DR


def example():
    sp=S.new_spec('Synthetic')
    D.add_polygon(sp,[[0,0],[200,0],[200,200],[0,200]],name='A')
    D.add_polygon(sp,[[300,0],[500,0],[500,100],[300,100]],name='B')
    sp['seams']=[{'id':'S1','enabled':True,'a':{'piece':'A','from':['E1',1.0],'to':['E1',0.0],'dir':-1},
                  'b':{'piece':'B','from':['E3',0.0],'to':['E3',1.0],'dir':1}}]
    new=copy.deepcopy(sp)
    DA.add_dart(new,'A','E1',.5,width=20,length=80,twins=False,true=False)
    return sp,new


def inputs(sp):
    pat,_=PB.build(sp,10);tris,bad=DR.triangulate(pat)
    assert not bad
    flat=np.vstack([pat[f'V{i}'] for i in range(len(pat['names']))])
    offs=np.cumsum([0]+[len(pat[f'V{i}']) for i in range(len(pat['names'])-1)])
    idx={'flat':flat,'offs':offs,'F':np.vstack([t+o for t,o in zip(tris,offs)]),
         'fp':np.concatenate([np.full(len(t),i) for i,t in enumerate(tris)])}
    return pat,tris,idx,np.c_[flat,np.zeros(len(flat))]


class SplitCarry(unittest.TestCase):
    def test_dart_keeps_material_positions_without_operation_memory(self):
        old,new=example();op,ot,idx,X=inputs(old);np_,nt,_,_=inputs(new)
        actual,_=TR.carry(old,new,op,idx,X,np_,nt)
        for i,n in enumerate(np_['names']):
            np.testing.assert_allclose(actual[str(n)],np.c_[np_[f'V{i}'],np.zeros(len(np_[f'V{i}']))],atol=1e-8)

    def test_partition_identifies_retained_fragment(self):
        old,new=example();self.assertEqual(TR._split_seam_ids(old,new),{'S1'})

    def test_reversed_pair_columns_are_supported(self):
        old,new=example()
        for sm in new['seams']:sm['a'],sm['b']=sm['b'],sm['a']
        self.assertEqual(TR._split_seam_ids(old,new),{'S1'})

    def test_shortened_seam_without_partition_keeps_old_rule(self):
        old,new=example();new['seams']=[next(s for s in new['seams'] if s['id']=='S1')]
        self.assertEqual(TR._split_seam_ids(old,new),set())
        self.assertTrue(any(sn['piece']=='A' for _,sn,_,_,_ in TR._sewn_sides(old,new)))

    def test_disabled_continuation_is_not_a_partition(self):
        old,new=example()
        for sm in new['seams']:
            if sm['id']!='S1':sm['enabled']=False
        self.assertEqual(TR._split_seam_ids(old,new),set())

    def test_overlap_is_ambiguous(self):
        old,new=example();dup=copy.deepcopy(new['seams'][0]);dup['id']='duplicate';new['seams'].append(dup)
        self.assertEqual(TR._split_seam_ids(old,new),set())

    def test_partition_on_partner_does_not_hide_overlapping_reattachments(self):
        old,new=example()
        for sm in new['seams']:
            if sm['b']['piece']=='B':sm['a'].update({'from':['E2',1.0],'to':['E2',0.0],'dir':-1})
        self.assertEqual(TR._split_seam_ids(old,new),set())
        self.assertTrue(TR._sewn_sides(old,new))

    def test_changed_partner_is_not_an_anchor(self):
        old,new=example();D.move_piece(new,'B',shift=(1.,0.))
        self.assertEqual(TR._split_seam_ids(old,new),set())

    def test_missing_retained_id_is_not_inferred(self):
        old,new=example();new['seams'][0]['id']='replacement'
        self.assertEqual(TR._split_seam_ids(old,new),set())

    def test_second_dart_partitions_the_same_original_seam(self):
        old,new=example();DA.add_dart(new,'A','E1',.4,width=12,length=60,twins=False,true=False)
        self.assertEqual(TR._split_seam_ids(old,new),{'S1'})
        op,ot,idx,X=inputs(old);np_,nt,_,_=inputs(new)
        actual,_=TR.carry(old,new,op,idx,X,np_,nt)
        self.assertGreater(float(TR.tri_areas_3d(actual['A'],nt[0]).min()),TR.MIN_AREA_MM2)


if __name__=='__main__':
    res=unittest.main(argv=['test'],exit=False,verbosity=2).result
    if len(args)>1:Path(args[1]).write_text(json.dumps({'ok':res.wasSuccessful(),'tests':res.testsRun,'failures':len(res.failures),'errors':len(res.errors)}),encoding='utf8')
    sys.exit(0 if res.wasSuccessful() else 1)
