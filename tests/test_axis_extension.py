# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import unittest
import numpy as np
from ac9_cloth_sim.core import spec as S,draft as D,extend as E,iline as IL,internal_sync as IS


class AxisExtension(unittest.TestCase):
    def fixture(self,pts=None):
        sp=S.new_spec('axis extension')
        D.add_polygon(sp,pts or [[0,0],[100,0],[100,80],[0,80]],name='P')
        self.assertFalse(D.problems(sp,['P']))
        return sp

    def test_top_bottom_and_left_right(self):
        for axis,edges,minimum,maximum,area in [('Y',['E1','E3'],[0,-10],[100,90],10000),('X',['E2','E4'],[-10,0],[110,80],9600)]:
            sp=self.fixture();before=S.dumps(sp)
            out,_,lines=E.plan_axis(sp,{'P':edges},axis,10,twins=False)
            pc=D.piece(out,'P');P=D.corners(pc)
            np.testing.assert_allclose(P.min(0),minimum);np.testing.assert_allclose(P.max(0),maximum)
            self.assertEqual(D.signed_area(E._ring(pc)),area)
            self.assertEqual(len(lines['P']),2);self.assertEqual(len(pc['edges']),8)
            self.assertEqual(S.dumps(sp),before)
            self.assertEqual(S.dumps(S.loads(S.dumps(out))),S.dumps(out))

    def test_sloped_curve_keeps_shape_and_axis(self):
        sp=self.fixture([[0,0],[100,20],[100,80],[0,80]])
        edge=D.piece(sp,'P')['edges'][0];edge['ctrl']=[[20,-10],[75,0]];D.refresh_curve(edge)
        before=copy.deepcopy(edge)
        out,_,lines=E.plan_axis(sp,{'P':['E1']},'Y',12,twins=False)
        pc=D.piece(out,'P');edge=next(x for x in pc['edges'] if x['id']=='E1')
        np.testing.assert_allclose(np.asarray(edge['pts'])-before['pts'],np.tile([0,-12],(len(edge['pts']),1)))
        np.testing.assert_allclose(np.asarray(edge['ctrl'])-before['ctrl'],[[0,-12],[0,-12]])
        old=D.internal(pc,lines['P'][0]);np.testing.assert_allclose(old['pts'],before['pts'])
        np.testing.assert_allclose(IL.shape(old)[1][0],before['ctrl'])

    def test_wrapped_connected_run_stays_together(self):
        sp=self.fixture([[50,0],[100,0],[100,80],[0,80],[0,0]])
        out,_,lines=E.plan_axis(sp,{'P':['E5','E1']},'Y',10,twins=False)
        self.assertEqual(len(lines['P']),1)
        pc=D.piece(out,'P')
        for eid in ['E5','E1']:self.assertEqual(pc['edges'][D.edge_index(pc,eid)]['pts'][0][1],-10)

    def test_multipiece_each_has_own_outward(self):
        sp=self.fixture();D.add_polygon(sp,[[200,0],[300,0],[300,80],[200,80]],name='Q')
        out,names,_=E.plan_axis(sp,{'P':['E1'],'Q':['E3']},'Y',10,twins=False)
        self.assertEqual(names,['P','Q'])
        for nm,eid,y in [('P','E1',-10),('Q','E3',90)]:
            pc=D.piece(out,nm);self.assertEqual(pc['edges'][D.edge_index(pc,eid)]['pts'][0][1],y)

    def test_rejections_are_atomic_and_xy_unchanged(self):
        sp=self.fixture();before=S.dumps(sp)
        for edges,axis,d in [(['E2'],'Y',10),(['E1'],'Z',10),(['E1'],'Y',0),(['E1'],'Y',-1),(['E1'],'Y',float('nan')),(['E1','E2','E3','E4'],'Y',10),(['E2','E3'],'Y',10)]:
            with self.assertRaises(ValueError):E.plan_axis(sp,{'P':edges},axis,d,twins=False)
            self.assertEqual(S.dumps(sp),before)
        out,_,_=E.plan(sp,{'P':['E2','E3']},[10,10],twins=False)
        self.assertEqual(D.signed_area(E._ring(D.piece(out,'P'))),9800)
        with self.assertRaises(ValueError):E.plan(sp,{'P':['E1','E3']},[0,-10],twins=False)

    def test_seams_off_material_and_on_outline(self):
        sp=self.fixture();D.add_polygon(sp,[[200,0],[300,0],[300,80],[200,80]],name='Q')
        sp['seams']=[dict(id='S1',a=dict(piece='P',**{'from':['E1',0],'to':['E1',1],'dir':1}),b=dict(piece='Q',**{'from':['E3',0],'to':['E3',1],'dir':1}),enabled=True)]
        for on in [False,True]:
            out,_,_=E.plan_axis(sp,{'P':['E1'],'Q':['E3']},'Y',10,on,twins=False)
            seam=out['seams'][0]
            for key,oldy,newy in [('a',0,-10),('b',80,90)]:
                np.testing.assert_allclose(S.side_polyline(out,seam[key])[:,1],newy if on else oldy)
                self.assertEqual('line' in seam[key],not on)
            self.assertEqual(len(out['seams']),1)

    def test_mirror_both_explicit_and_legacy_lines(self):
        sp=self.fixture();old=D.add_internal(sp,'P',[[0,0],[100,0]],mirror=False)
        other=D.mirror_copy(sp,'P')
        for pc in sp['pieces']:
            for line in pc['internals']:line.pop('mirror_id',None)
        peer=IS.match(sp,'P',old)
        out,_,lines=E.plan_axis(sp,{'P':['E1']},'Y',10)
        self.assertEqual(IS.match(out,'P',old),peer)
        self.assertEqual(IS.match(out,'P',lines['P'][0]),(other,lines[other][0]))
        # Both explicitly selected mirrored outlines agree for the axis mode.
        pair_ids={nm:['E2','E4'] for nm in ['P',other]}
        out,names,_=E.plan_axis(sp,pair_ids,'X',10)
        self.assertEqual(set(names),{'P',other})

    def test_self_mirror_keeps_opposite_axis_directions(self):
        sp=self.fixture();D.piece(sp,'P')['mirror_edit']=True
        out,_,lines=E.plan_axis(sp,{'P':['E2']},'X',10)
        pc=D.piece(out,'P');self.assertEqual(len(lines['P']),2)
        np.testing.assert_allclose(D.corners(pc).min(0),[-10,0])
        np.testing.assert_allclose(D.corners(pc).max(0),[110,80])

    def test_combined_mirrors_reach_all_edges_without_duplicates(self):
        for mode in ['XY','AXIS']:
            sp=self.fixture();D.piece(sp,'P')['mirror_edit']=True
            other=D.mirror_copy(sp,'P');before=S.dumps(sp)
            if mode=='AXIS':out,_,lines=E.plan_axis(sp,{'P':['E2']},'X',10)
            else:out,_,lines=E.plan(sp,{'P':['E2']},[10,0])
            out=S.loads(S.dumps(out))
            for nm in ['P',other]:
                pc=D.piece(out,nm)
                self.assertEqual(float(np.ptp(D.corners(pc),axis=0)[0]),120)
                self.assertEqual(len(pc['edges']),8);self.assertEqual(len(pc['internals']),2)
                self.assertIsNotNone(D.twin_corner_map(out,nm)[1])
                for lid in lines[nm]:self.assertIsNotNone(IS.match(out,nm,lid))
            self.assertEqual(S.dumps(sp),before)
            both={nm:['E2','E4'] for nm in ['P',other]}
            out,_,_=E.plan_axis(sp,both,'X',10)
            self.assertEqual([len(D.piece(out,nm)['edges']) for nm in both],[8,8])
            with self.assertRaises(ValueError):E.plan(sp,{'P':['E2','E4']},[10,0])
            self.assertEqual(S.dumps(sp),before)

    def test_invalid_last_piece_rolls_back_all(self):
        sp=self.fixture();D.add_polygon(sp,[[200,0],[300,0],[300,80],[200,80]],name='Q');before=S.dumps(sp)
        with self.assertRaises(ValueError):E.plan_axis(sp,{'P':['E1'],'Q':['E2']},'Y',10,twins=False)
        self.assertEqual(S.dumps(sp),before)


if __name__=='__main__':unittest.main()
