"""Ordered topology and temporary builder ownership for host preparation."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'solver'))
from native_mesh_prepare import boundary_edges,cloth_edges,triangle_rows,bulk_cloth_edges


class MeshPrepareTests(unittest.TestCase):
    def test_edges_keep_first_triangle_order_and_winding(self):
        faces=np.array([[0,1,2],[2,1,3]],np.int32)
        expected=np.array([[2,-1,0,1],[0,3,1,2],[1,-1,2,0],[2,-1,1,3],[1,-1,3,2]],np.int32)
        self.assertEqual(cloth_edges(faces).tobytes(),expected.tobytes())
        self.assertEqual(list(boundary_edges(faces).values()),[(0,1,2),(2,0,1),(1,3,2),(3,2,1)])
        self.assertEqual(cloth_edges([]).shape,(0,4))
        self.assertEqual(boundary_edges([]),{})

    def test_nonmanifold_requests_original_path(self):
        self.assertIsNone(cloth_edges([[0,1,2],[1,0,3],[0,1,4]]))

    def test_triangle_incidence_keeps_duplicates_and_ring_update_order(self):
        faces=np.array([[0,1,2],[2,1,3],[0,0,4]],np.int64)
        incidence,ring=triangle_rows(faces,5)
        self.assertEqual(incidence[0],[0,2,2])
        expected=[set() for _ in range(5)]
        for a,b,c in faces:
            expected[a].update((b,c));expected[b].update((a,c));expected[c].update((a,b))
        for i in range(5):self.assertEqual(list(ring[i]),list(expected[i]))

    def test_instance_method_restored_after_failure(self):
        class Builder:
            def _add_soft_mesh_edges_from_triangles(self,*args,**kwargs):return None
        Builder._add_soft_mesh_edges_from_triangles.__module__='newton._src.sim.builder'
        builder=Builder()
        with self.assertRaisesRegex(RuntimeError,'injected'):
            with bulk_cloth_edges(builder):
                self.assertIn('_add_soft_mesh_edges_from_triangles',vars(builder))
                raise RuntimeError('injected')
        self.assertNotIn('_add_soft_mesh_edges_from_triangles',vars(builder))


if __name__=='__main__':unittest.main()
