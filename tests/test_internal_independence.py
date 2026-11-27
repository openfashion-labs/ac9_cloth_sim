"""The approved independent internal-line contract, including saved legacy links."""
import copy,unittest
from pathlib import Path
import numpy as np
from ac9_cloth_sim.core import spec as S,draft as D,iline as IL,boundary_connection as B,point_connection as PC

class InternalIndependence(unittest.TestCase):
    def fixture(self):
        sp=S.new_spec('Independent');D.add_polygon(sp,[[0,0],[200,0],[200,200],[0,200]],name='P')
        a=D.add_internal(sp,'P',[[40,60],[160,60]],mirror=False,authored=True)
        b=D.add_internal(sp,'P',[[40,60],[100,140]],mirror=False,authored=True)
        c=D.add_internal(sp,'P',[[100,60],[160,140]],mirror=False,authored=True)
        self.assertEqual(D.problems(sp,['P'])+D.internal_problems(sp,'P'),[])
        return sp,(a,b,c)
    def point(self,sp,lid,k=0):return IL.shape(D.internal(D.piece(sp,'P'),lid))[0][k]
    def test_coincident_and_edge_contacts_do_not_follow(self):
        for api in ('node','endpoint','whole','transform'):
            sp,(a,b,c)=self.fixture();old=[self.point(sp,l).copy() for l in (b,c)]
            if api=='node':IL.move_node(sp,'P',a,0,[0,10])
            elif api=='endpoint':D.move_internal(sp,'P',a,'start',[0,10])
            elif api=='whole':D.move_internal(sp,'P',a,'all',[0,10])
            else:D.transform_group(sp,lines=[('P',a)],shift=[0,10],deg=10)
            np.testing.assert_allclose([self.point(sp,l) for l in (b,c)],old,atol=1e-4)
    def test_legacy_tokens_are_preserved_without_behavior(self):
        for token in ('previous-group',False,None):
            sp,(a,b,c)=self.fixture()
            if token is not None:
                for lid in (a,b):D.internal(D.piece(sp,'P'),lid)['vertex_groups']={'0':token}
            raw=S.dumps(sp);sp=S.loads(raw);self.assertEqual(S.dumps(sp),raw)
            self.assertEqual(B.groups(D.piece(sp,'P')),[])
            D.move_internal(sp,'P',a,'start',[10,0]);np.testing.assert_allclose(self.point(sp,b),[40,60])
            if token is not None:self.assertEqual(D.internal(D.piece(sp,'P'),b)['vertex_groups'],{'0':token})
    def test_explicit_multi_selection_still_moves_each_line_once(self):
        sp,(a,b,c)=self.fixture();D.transform_group(sp,lines=[('P',a),('P',b)],shift=[10,5])
        for lid in (a,b):np.testing.assert_allclose(self.point(sp,lid),[50,65],atol=1e-4)
        np.testing.assert_allclose(self.point(sp,c),[100,60],atol=1e-4)
    def test_no_retired_runtime_modules_in_addon(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('core/vertex_connection.py','vertex_picker.py','archive'):
            self.assertFalse((root/name).exists(),name)
    def test_shared_outline_corner_still_carries_all_attached_lines(self):
        sp,ls=self.fixture();pc=D.piece(sp,'P')
        for lid in ls[:2]:
            D.internal(pc,lid)['pts'][0]=[0,0];self.assertTrue(PC.bind(pc,lid,0))
        D.move_corners(sp,'P',{0:[10,5]})
        for lid in ls[:2]:np.testing.assert_allclose(self.point(sp,lid),[10,5],atol=1e-4)
    def test_detaching_outline_leaves_remaining_lines_independent(self):
        sp,ls=self.fixture();pc=D.piece(sp,'P')
        for lid in ls[:2]:D.internal(pc,lid)['pts'][0]=[0,0]
        B.detach_pair(sp,'P',('corner','E1'));IL.move_node(sp,'P',ls[0],0,[10,0])
        np.testing.assert_allclose(self.point(sp,ls[1]),[0,0]);np.testing.assert_allclose(D.corners(pc)[0],[0,0])
    def test_dense_explicit_outline_endpoint_is_in_same_group_and_detaches(self):
        sp,_=self.fixture();lid=D.add_internal(sp,'P',np.linspace([0,0],[150,150],25),mirror=False)
        pc=D.piece(sp,'P');self.assertFalse(IL.editable(D.internal(pc,lid)))
        self.assertTrue(PC.bind(pc,lid,0));self.assertIn(('internal',lid,0),B.members(pc,('corner','E1')))
        B.detach_pair(sp,'P',('corner','E1'));self.assertIs(D.internal(pc,lid)['boundary_nodes']['0'],False)

if __name__=='__main__':unittest.main()
