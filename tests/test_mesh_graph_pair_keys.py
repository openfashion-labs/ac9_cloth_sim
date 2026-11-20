"""Pair-key sorting keeps exact sparse row means, including multiplicity."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np
spec=importlib.util.spec_from_file_location('mesh_graph',Path(__file__).resolve().parents[1]/'solver/mesh_graph.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class PairKeys(unittest.TestCase):
    def test_unique_pairs_match_lexicographic_order_and_counts(self):
        rng=np.random.default_rng(17)
        for dtype in (np.int32,np.int64):
            for size in (0,1,1000):
                r=rng.integers(0,71,size,dtype=dtype);c=rng.integers(0,53,size,dtype=dtype)
                expected=np.unique(np.column_stack([r,c]),axis=0,return_counts=True)
                actual=m._unique_pairs(r,c,True)
                for a,b in zip(actual,expected):
                    self.assertEqual(a.dtype,b.dtype);np.testing.assert_array_equal(a,b)
    def test_overflow_and_negative_use_safe_fallback(self):
        limit=np.iinfo(np.intp).max
        for r,c in (([1,0],[limit,limit-1]),([limit,limit],[2,1]),([-1,0],[1,0])):
            r=np.array(r,dtype=np.intp);c=np.array(c,dtype=np.intp)
            np.testing.assert_array_equal(m._unique_pairs(r,c),np.unique(np.column_stack([r,c]),axis=0))
    def test_repeated_rows_preserve_multiplicity(self):
        mean=m.RowMean(np.array([0,0,0,2]),np.array([1,1,2,0]),4)
        np.testing.assert_array_equal(mean.rows,[0,0,2]);np.testing.assert_array_equal(mean.columns,[1,2,0])
        np.testing.assert_array_equal(mean.weights,[2/3,1/3,1]);np.testing.assert_array_equal(mean.starts,[0,2,2,3,3])
    def test_empty_and_isolated_mesh(self):
        adjacency,faces=m.mesh_averages(np.empty((0,3),np.intp),5)
        for mean in (adjacency,faces):
            self.assertEqual(len(mean.rows),0);np.testing.assert_array_equal(mean.starts,np.zeros(6))
    def test_duplicate_degenerate_faces(self):
        faces=np.array([[0,1,2],[0,1,2],[2,2,3]],np.intp)
        adjacency,mean=m.mesh_averages(faces,5)
        pairs=np.unique(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]],faces[:,[1,0]],faces[:,[2,1]],faces[:,[0,2]]]),axis=0)
        np.testing.assert_array_equal(np.column_stack([adjacency.rows,adjacency.columns]),pairs)
        expected=np.unique(np.column_stack([faces.ravel(),np.repeat(np.arange(3),3)]),axis=0,return_counts=True)
        np.testing.assert_array_equal(np.column_stack([mean.rows,mean.columns]),expected[0])
if __name__=='__main__':unittest.main()
