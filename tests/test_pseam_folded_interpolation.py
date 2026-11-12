"""Geometric interpolation invariants; no Blender runtime or garment data."""
import ast
from pathlib import Path
import unittest
import numpy as np

tree=ast.parse((Path(__file__).resolve().parents[1]/'place_seam.py').read_text(encoding='utf8'))
scope={'np':np}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_bend','_bend_folded')],type_ignores=[]),'place_seam.py','exec'),scope)
bend,folded=scope['_bend'],scope['_bend_folded']

class FoldedInterpolation(unittest.TestCase):
    def setUp(self):
        self.X=np.array([[0.,0.,0.],[0.,20.,0.],[0.,40.,0.],[0.,0.,2.],[0.,20.,2.],[0.,40.,2.]])
        self.V=np.array([[0.,0.],[0.,20.],[0.,40.],[40.,0.],[40.,20.],[40.,40.]])
        self.vi=np.array([0,1,2])
        self.Q=self.X[self.vi]+np.array([[0.,0.,0.],[10.,0.,0.],[-5.,0.,0.]])

    def test_nearby_layers_receive_similar_displacements(self):
        old=bend(self.V,self.vi,self.X,self.Q)-self.X
        new=folded(self.V,self.vi,self.X,self.Q)-self.X
        self.assertLess(np.linalg.norm(new[:3]-new[3:]),np.linalg.norm(old[:3]-old[3:])*.05)

    def test_sewn_targets_still_reached(self):
        np.testing.assert_allclose(folded(self.V,self.vi,self.X,self.Q)[self.vi],self.Q,atol=1e-10,rtol=0)

    def test_rigid_coordinate_change_commutes(self):
        angle=.72;c,s=np.cos(angle),np.sin(angle)
        R=np.array([[c,-s,0],[s,c,0],[0,0,1.]])
        t=np.array([3.,-9.,1200.])
        np.testing.assert_allclose(folded(self.V,self.vi,self.X@R.T+t,self.Q@R.T+t),folded(self.V,self.vi,self.X,self.Q)@R.T+t,atol=1e-11,rtol=0)

    def test_same_displacement_is_preserved_everywhere(self):
        shift=np.array([13.,-7.,4.])
        np.testing.assert_allclose(folded(self.V,self.vi,self.X,self.X[self.vi]+shift),self.X+shift,atol=1e-12,rtol=0)

    def test_flat_kernel_remains_the_original_formula(self):
        dd=np.linalg.norm(self.V[:,None,:]-self.V[self.vi][None,:,:],axis=2)+1e-6
        w=1/dd**2
        np.testing.assert_array_equal(bend(self.V,self.vi,self.X,self.Q),self.X+(w@(self.Q-self.X[self.vi]))/w.sum(1,keepdims=True))

    def test_two_sewn_layers_keep_separate_targets(self):
        vi=np.arange(6)
        for gap in (0.,1e-7,1e-5,.001,1.):
            for opposite in (False,True):
                with self.subTest(gap=gap,opposite=opposite):
                    x=self.X.copy();x[3:]=x[:3];x[3:,2]+=gap
                    Q=x+np.array([[0.,0.,5.]]*3+[[0.,0.,-5. if opposite else 5.]]*3)
                    np.testing.assert_array_equal(folded(self.V,vi,x,Q),bend(self.V,vi,x,Q))
                    np.testing.assert_allclose(folded(self.V,vi,x,Q),Q,atol=1e-10,rtol=0)

    def test_duplicate_sewn_indices_keep_previous_aggregation(self):
        vi=np.array([0,0,1,2,3,3,4,5])
        Q=self.X[vi]+np.array([[0.,0.,z] for z in (5.,-5.,3.,4.,9.,-7.,1.,2.)])
        np.testing.assert_array_equal(folded(self.V,vi,self.X,Q)[np.unique(vi)],bend(self.V,vi,self.X,Q)[np.unique(vi)])

if __name__=='__main__':unittest.main()
