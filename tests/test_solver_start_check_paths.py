"""Known geometry tests for stitch-through checks, without Blender or GPU."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

spec=importlib.util.spec_from_file_location('solver_start_check',Path(__file__).resolve().parents[1]/'solver/start_check.py')
check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)

class StitchPaths(unittest.TestCase):
    def run_check(self, triangle, *, idle=False, reverse=False):
        x=np.array([[0,0,-1],[0,0,1],*triangle],dtype=float)
        e=np.array([[1,0] if reverse else [0,1]],dtype=np.int64)
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ,{'AC9_DRESS':'1'}):
            rep,action=check.check({},out,x,np.array([[2,3,4]]),np.array([2]),np.array([0,1,2,2,2]),
                                   ['A','B','C'],e,{0} if idle else set(),lambda p:np.zeros(len(p),bool))
        self.assertEqual(rep['problems'],[])
        self.assertEqual(action,'ok')
        return rep['through']

    def test_interior_crossing_and_reversed_stitch(self):
        triangle=[[-1,-1,0],[1,-1,0],[0,1,0]]
        for reverse in (False,True):
            self.assertEqual(self.run_check(triangle,reverse=reverse),{'A - B through C':1})

    def test_triangle_edge_is_inclusive(self):
        self.assertEqual(self.run_check([[0,-1,0],[1,0,0],[0,1,0]]),{'A - B through C':1})

    def test_triangle_vertex_is_inclusive(self):
        self.assertEqual(self.run_check([[0,0,0],[1,0,0],[0,1,0]]),{'A - B through C':1})

    def test_boxes_touch_but_triangle_does_not(self):
        self.assertEqual(self.run_check([[0,1,0],[1,0,0],[1,1,0]]),{})

    def test_parallel_degenerate_and_distant_triangles(self):
        for triangle in ([[0,0,-0.5],[0,0,0],[0,0,0.5]],
                         [[1,0,-1],[1,1,0],[1,0,1]],
                         [[-1,-1,3],[1,-1,3],[0,1,3]]):
            self.assertEqual(self.run_check(triangle),{})

    def test_stitch_endpoints_are_excluded(self):
        for z in (-1,1):
            self.assertEqual(self.run_check([[-1,-1,z],[1,-1,z],[0,1,z]]),{})

    def test_idle_stitch_is_excluded(self):
        self.assertEqual(self.run_check([[-1,-1,0],[1,-1,0],[0,1,0]],idle=True),{})

    def test_no_triangles_or_stitches(self):
        with tempfile.TemporaryDirectory() as out:
            rep,action=check.check({},out,np.zeros((1,3)),np.empty((0,3),int),np.array([],int),
                                   np.array([0]),['A'],np.empty((0,2),int),set(),lambda p:np.zeros(len(p),bool))
        self.assertEqual(rep['through'],{})
        self.assertIsNone(rep['stitch_gap_mm_p50_max'])
        self.assertEqual(action,'ok')

if __name__=='__main__':unittest.main()
