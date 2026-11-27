"""Partial placement edits preserve real result members even with an unknown basis.

Finite synthetic rectangles isolate ownership. No solver/physical-quality claim.
"""
import copy,json
from pathlib import Path
import sys,unittest
from unittest.mock import patch
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_fold_shared_mesh as FX
from ac9_cloth_sim import placement as PL,placed_view as PV,store as ST,dress as DR,display_state as DS,control_basis as CB,prepare_build as PB,pattern_view as PAT
from ac9_cloth_sim.core import draft as D,spec as S


class MixedResult(unittest.TestCase):
    co=FX.SharedFold.co
    def setUp(self):
        FX.SharedFold.setUp(self)

    def result(self):
        bpy.data.objects.remove(self.obs[1],do_unlink=True)
        flat=np.vstack([self.V,self.V]);faces=np.vstack([self.T,self.T+4])
        xyz=np.vstack([np.c_[self.V,np.full(4,20.)],np.c_[self.V,np.full(4,40.)]])
        g=DR.garment_object(self.ctx,DR.garment_name(self.sp),flat,faces,np.array([0,0,1,1]),xyz,['A','B'],editing_basis={'A':CB.PLACED,'B':CB.UNKNOWN})
        g['ac9_reset_pieces']=json.dumps(['A']);DS.apply(self.ctx,self.sp)
        self.assertFalse(DR.has_result(g))
        self.assertEqual(DS.snapshot(self.sp,g)['B']['source'],'result')
        return g

    def prepared(self,three=False):
        vs=[self.V,self.V]+([self.V+[300,0]] if three else [])
        return dict(pat=dict(names=['A','B']+(['C'] if three else []),**{'V'+str(i):v for i,v in enumerate(vs)}),
            tris=[self.T]*len(vs),X=[np.c_[v,np.full(4,5.)] for v in vs],pairs=[])

    def test_partial_placement_keeps_unselected_result_and_basis(self):
        g=self.result();old=np.array([v.co[:] for v in g.data.vertices])
        with patch.object(DR,'prepare',return_value=self.prepared()):
            PL.rebuild_placement(self.ctx,self.sp,['A'])
        self.assertNotIn('B',PV.objects(self.sp))
        self.assertEqual(DS.snapshot(self.sp,g)['B']['source'],'result')
        self.assertEqual(CB.basis(g)['B'],CB.UNKNOWN)
        np.testing.assert_array_equal(np.array([v.co[:] for v in g.data.vertices]),old)

    def test_whole_placement_still_regenerates_all_existing_placements(self):
        with patch.object(DR,'prepare',return_value=self.prepared()):
            PL.rebuild_placement(self.ctx,self.sp,['A'])
        b=PV.objects(self.sp)['B'];self.ctx.view_layer.update()
        np.testing.assert_allclose([float((b.matrix_world@v.co).z)*1000 for v in b.data.vertices],5.,atol=.00001)

    def test_selected_result_refusal_with_no_job_rolls_back_settings(self):
        g=self.result();old=self.text.as_string();candidate=copy.deepcopy(self.sp)
        candidate['placement']['B']['anchor']=[60,50]
        with patch.object(DR,'reset_to_placed',side_effect=ValueError('private missing result')) as reset:
            with self.assertRaisesRegex(ValueError,'private missing result'):
                PL._remade(self.ctx,self.sp,candidate,['B'])
        self.assertEqual(reset.call_count,1);self.assertEqual(self.text.as_string(),old)
        self.assertNotIn('B',PV.objects(ST.get(self.ctx)[0]));self.assertEqual(CB.basis(g)['B'],CB.UNKNOWN)

    def test_new_piece_can_be_materialized_without_consuming_old_results(self):
        g=self.result();old=np.array([v.co[:] for v in g.data.vertices])
        sp=copy.deepcopy(self.sp);D.add_polygon(sp,(self.V+[300,0]).tolist(),name='C')
        sp['placement']['C']=copy.deepcopy(sp['placement']['A'])
        ST.put(self.ctx,sp);PAT.rebuild(self.ctx,*ST.get(self.ctx))
        with patch.object(DR,'prepare',return_value=self.prepared(True)):
            with PB.operation(self.ctx) as owner:
                _,made=PL.give_shape(self.ctx,sp,['C'],preparation=owner)
        self.assertEqual(made,['C']);self.assertIn('C',PV.objects(ST.get(self.ctx)[0]))
        self.assertNotIn('B',PV.objects(ST.get(self.ctx)[0]));self.assertEqual(CB.basis(g)['B'],CB.UNKNOWN)
        np.testing.assert_array_equal(np.array([v.co[:] for v in g.data.vertices]),old)
