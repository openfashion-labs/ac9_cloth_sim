# SPDX-License-Identifier: GPL-3.0-or-later
"""Numerical contracts for the SciPy-free solver helpers."""
import importlib.util
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'solver'))
from dense_opt import nnls
from layer_patch_active import feasible_point,solve_dense_qp
from mesh_graph import mesh_averages
from spatial import KDTree


class PatchMathTests(unittest.TestCase):
    def test_nnls_small_rhs_and_scale_invariance(self):
        for matrix_scale in (1.e-16,1.,1.e16):
            for rhs_scale in (1.e-16,1.,1.e16):
                x,residual=nnls(np.eye(2)*matrix_scale,np.array([rhs_scale,0.]))
                np.testing.assert_allclose(x,[rhs_scale/matrix_scale,0.],rtol=1.e-15,atol=0.)
                self.assertEqual(residual,0.)

    def test_feasible_point_small_constraint(self):
        # A fixed Phase-I regularization of 1e-6 incorrectly refuses this
        # exactly feasible corner. Retry must preserve the original bounds.
        n=30;trust=.02
        a=np.vstack([np.full((1,n),1.e-8),np.eye(n),-np.eye(n)])
        rhs=np.r_[6.e-9,np.full(2*n,-trust)]
        result=feasible_point(a,rhs,np.zeros(n))
        self.assertTrue(result.success)
        self.assertLessEqual(float(np.max(rhs-a@result.x)),1.e-9)

    def test_qp_feasible_infeasible_and_warm(self):
        a=np.array([[1.,0.],[0.,1.]])
        result=solve_dense_qp(np.eye(2),np.zeros(2),a,np.array([.01,.015]),.02)
        self.assertTrue(result.success,result.message)
        np.testing.assert_allclose(result.x,[.01,.015],atol=1.e-9,rtol=0.)
        warm=solve_dense_qp(np.eye(2),np.zeros(2),a,np.array([.01,.015]),.02,
                            feasible_start=result.x,working_start=result.working_set)
        self.assertTrue(warm.success,warm.message)
        np.testing.assert_allclose(warm.x,result.x,atol=1.e-10,rtol=0.)
        bad=solve_dense_qp(np.eye(2),np.zeros(2),a,np.array([.03,.015]),.02)
        self.assertFalse(bad.success)

    def test_nnls_kkt_and_dependent_columns(self):
        rng=np.random.default_rng(6451)
        for m,n in ((3,1),(3,9),(20,10),(30,60)):
            a=rng.normal(size=(m,n));b=rng.normal(size=m)
            if n>1:a[:,1]=a[:,0]
            x,residual=nnls(a,b,maxiter=10000)
            gradient=a.T@(a@x-b)
            self.assertGreaterEqual(float(x.min()),0.)
            self.assertGreaterEqual(float(gradient.min()),-1.e-8)
            self.assertLess(float(np.max(np.abs(x*gradient))),1.e-8)
            self.assertAlmostEqual(residual,float(np.linalg.norm(a@x-b)))

    def test_graph_mask_and_isolated_vertices(self):
        average,face=mesh_averages(np.array([[0,1,2],[3,4,5]]),7)
        values=np.arange(7,dtype=float)
        np.testing.assert_array_equal(average@values,[1.5,1.,.5,4.5,4.,3.5,0.])
        np.testing.assert_array_equal(face@np.array([2.,3.]),[2.,2.,2.,3.,3.,3.,0.])
        count,labels=average.components(np.ones(7,bool))
        self.assertEqual(count,3);np.testing.assert_array_equal(labels,[0,0,0,1,1,1,2])
        count,labels=average.components(np.array([True,False,True,False,False,False,True]))
        self.assertEqual(count,2);np.testing.assert_array_equal(labels,[0,0,1])
        count,labels=average.components(np.zeros(7,bool))
        self.assertEqual(count,0);self.assertEqual(len(labels),0)


@unittest.skipUnless(importlib.util.find_spec('warp'),'Warp is a solver runtime dependency')
class SpatialTests(unittest.TestCase):
    def test_brute_force_distance_index_bound_and_shapes(self):
        rng=np.random.default_rng(512)
        for dimension in (2,3):
            for n in (0,1,17,240):
                points=rng.normal(size=(n,dimension));q=rng.normal(size=(19,dimension))
                tree=KDTree(points)
                for k in (1,8,16,33):
                    for bound in (.4,np.inf):
                        distance,index=tree.query(q,k=k,distance_upper_bound=bound)
                        d=np.linalg.norm(q[:,None,:]-points[None,:,:],axis=2)
                        order=np.argsort(d,axis=1,kind='stable')[:,:k]
                        expected=np.take_along_axis(d,order,axis=1)
                        expected=np.pad(expected,((0,0),(0,max(0,k-n))),constant_values=np.inf)
                        order=np.pad(order,((0,0),(0,max(0,k-n))),constant_values=n)
                        valid=expected<bound
                        expected=np.where(valid,expected,np.inf);order=np.where(valid,order,n)
                        if k==1:expected=expected[:,0];order=order[:,0]
                        np.testing.assert_allclose(distance,expected,atol=1.e-14,rtol=1.e-14)
                        np.testing.assert_array_equal(index,order)
                self.assertEqual(tree.query(q[:0])[0].shape,(0,))
                self.assertEqual(tree.query(q.reshape(1,19,dimension),k=8)[0].shape,(1,19,8))

    def test_ball_ties_scalar_and_radius_boundary(self):
        tree=KDTree([[0.,0.],[0.,0.],[1.,0.],[-1.,0.]])
        distance,index=tree.query([0.,0.],k=4)
        np.testing.assert_array_equal(distance,[0.,0.,1.,1.])
        np.testing.assert_array_equal(index,[0,1,2,3])
        self.assertEqual(tree.query_ball_point([0.,0.],0.),[0,1])
        self.assertEqual(tree.query_ball_point([0.,0.],1.),[0,1,2,3])
        self.assertEqual(tree.query_ball_point([[0.,0.],[0.,0.]],[0.,1.]).tolist(),[[0,1],[0,1,2,3]])
        d,i=tree.query([0.,0.],distance_upper_bound=0.)
        self.assertTrue(np.isinf(d));self.assertEqual(int(i),4)


if __name__=='__main__':unittest.main()
