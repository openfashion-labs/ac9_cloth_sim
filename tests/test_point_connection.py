# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import unittest
import numpy as np
from ac9_cloth_sim.core import draft as D, iline as IL, spec as S, point_connection as PC


class PointConnection(unittest.TestCase):
    def fixture(self, nodes=None):
        sp = S.new_spec('connection')
        D.add_polygon(sp, [[0,0],[200,0],[200,200],[0,200]], name='P')
        lid = D.add_internal(sp, 'P', nodes or [[0,0],[100,100]], mirror=False)
        pc = D.piece(sp, 'P')
        self.assertEqual(abs(D.signed_area(S.Outline(pc).B)), 40000)
        self.assertTrue(np.isfinite(S.Outline(pc).B).all())
        return sp, pc, lid

    def assertXY(self, actual, expected):
        np.testing.assert_allclose(actual, expected, atol=1e-5)

    def test_internal_moves_outline_and_other_connected_line_once(self):
        sp, pc, lid = self.fixture()
        other = D.add_internal(sp, 'P', [[0,0],[50,100]], mirror=False)
        IL.move_node(sp, 'P', lid, 0, [20,0])
        self.assertXY(D.corners(pc)[0], [20,0])
        self.assertXY(IL.shape(D.internal(pc,lid))[0][0], [20,0])
        self.assertXY(IL.shape(D.internal(pc,other))[0][0], [20,0])

    def test_outline_moves_connected_endpoint(self):
        sp, pc, lid = self.fixture()
        D.move_corners(sp,'P',{0:[0,-20]},twins=False)
        self.assertXY(IL.shape(D.internal(pc,lid))[0][0], [0,-20])

    def test_two_connected_ends_do_not_accumulate_transforms(self):
        sp, pc, lid = self.fixture([[0,0],[100,0],[200,0]])
        D.move_corners(sp,'P',{0:[0,-20],1:[0,-20]},twins=False)
        self.assertXY(IL.shape(D.internal(pc,lid))[0], [[0,-20],[100,-20],[200,-20]])

    def test_detach_point_two_tenths_survives_save_and_next_move(self):
        sp, pc, lid = self.fixture()
        PC.detach_pair(sp,'P',lid,0); IL.move_node(sp,'P',lid,0,[.2,0])
        self.assertXY(D.corners(pc)[0], [0,0])
        sp=S.loads(S.dumps(sp)); pc=D.piece(sp,'P')
        D.move_corners(sp,'P',{0:[0,-20]},twins=False)
        self.assertXY(IL.shape(D.internal(pc,lid))[0][0], [.2,0])

    def test_reconnect_is_explicit(self):
        sp, pc, lid=self.fixture(); PC.detach_pair(sp,'P',lid,0)
        IL.move_node(sp,'P',lid,0,[.2,0]); IL.move_node(sp,'P',lid,0,[-.2,0])
        self.assertIsNone(PC.corner(pc,D.internal(pc,lid),0))
        self.assertTrue(PC.bind(pc,lid,0))
        IL.move_node(sp,'P',lid,0,[10,0]); self.assertXY(D.corners(pc)[0],[10,0])

    def test_edge_interior_keeps_historical_follow(self):
        sp, pc, lid=self.fixture([[80,.5],[80,150]])
        D.move_corners(sp,'P',{0:[0,-20],1:[0,-20]},twins=False)
        self.assertXY(IL.shape(D.internal(pc,lid))[0][0],[80,-19.5])

    def test_read_does_not_change_old_document_or_bind_near_corner(self):
        sp, pc, lid=self.fixture([[.5,0],[80,100]]); old=S.dumps(sp)
        self.assertIsNone(PC.corner(pc,D.internal(pc,lid),0))
        self.assertEqual(S.dumps(sp),old)

    def test_node_insertion_keeps_detach_identity(self):
        sp, pc, lid=self.fixture([[100,100],[0,0]])
        PC.detach(pc,lid,1); IL.insert_node(sp,'P',lid,[50,50])
        self.assertIs(D.internal(pc,lid)['boundary_nodes']['2'],False)

    def test_mirror_insertion_keeps_detach_identity_on_both_lines(self):
        sp, pc, lid=self.fixture([[100,100],[0,0]])
        other=D.mirror_copy(sp,'P'); peer=D.mirror_lines(sp,'P',lid)[0][1]
        PC.detach_pair(sp,'P',lid,1); IL.insert_node(sp,'P',lid,[50,50])
        for nm, l in [('P',lid),(other,peer)]:
            line=D.internal(D.piece(sp,nm),l)
            self.assertIs(line['boundary_nodes']['2'],False)
            self.assertIsNone(PC.corner(D.piece(sp,nm),line,2))

    def test_whole_line_move_preserves_its_connection(self):
        sp, pc, lid=self.fixture();PC.bind(pc,lid,0)
        D.move_internal(sp,'P',lid,'all',[10,5])
        self.assertXY(D.corners(pc)[0],[10,5])
        self.assertXY(IL.shape(D.internal(pc,lid))[0][0],[10,5])

    def test_mirror_reconnect_restores_both_endpoints(self):
        sp,pc,lid=self.fixture(); other=D.mirror_copy(sp,'P')
        peer=D.mirror_lines(sp,'P',lid)[0][1]
        PC.detach_pair(sp,'P',lid,0); self.assertTrue(PC.bind_pair(sp,'P',lid,0))
        self.assertIsNotNone(PC.corner(pc,D.internal(pc,lid),0))
        self.assertIsNotNone(PC.corner(D.piece(sp,other),D.internal(D.piece(sp,other),peer),0))

    def test_whole_line_rotate_keeps_connected_corner_at_same_target(self):
        sp, pc, lid=self.fixture();PC.bind(pc,lid,0)
        f=D.group_map(15,center=[100,100])
        D.transform_group(sp,[],[('P',lid)],deg=15,center=[100,100])
        self.assertXY(D.corners(pc)[0],D._r(f([[0,0]]))[0])
        self.assertXY(IL.shape(D.internal(pc,lid))[0][0],D.corners(pc)[0])

    def test_two_whole_lines_share_one_transformed_corner(self):
        for deg,k,shift in [(0,(1,1),(10,0)),(15,(1,1),(0,0)),(0,(.9,.9),(0,0))]:
            with self.subTest(deg=deg,k=k,shift=shift):
                sp,pc,lid=self.fixture()
                other=D.add_internal(sp,'P',[[0,0],[60,80]],mirror=False)
                f=D.group_map(deg,k,shift,center=(100,100))
                D.transform_group(sp,[],[('P',lid),('P',other)],deg,k,shift,center=(100,100))
                wanted=D._r(f([[0,0]]))[0]
                self.assertXY(D.corners(pc)[0],wanted)
                for l in (lid,other):self.assertXY(IL.shape(D.internal(pc,l))[0][0],wanted)


if __name__=='__main__': unittest.main()
