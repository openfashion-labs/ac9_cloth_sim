"""Selected rims follow the displayed mesh boundary, including holes and deformations."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from ac9_cloth_sim import piece_look as PK


class CurvedRim(unittest.TestCase):
    def setUp(self):
        PK.invalidate()
        x=np.linspace(0.,300.,7);theta=np.linspace(-2.3,2.3,7)
        self.F=np.array([(u,z) for z in (0.,30.) for u in x])
        self.W=np.array([(np.cos(t)*.1,np.sin(t)*.1,z) for z in (0.,.03) for t in theta])
        self.T=np.array([tri for i in range(6) for tri in ((i,i+1,i+8),(i,i+8,i+7))])
        self.spec={'pieces':[{'name':'P'}]}
        self.out={'P':SimpleNamespace(B=np.array([[0.,0.],[300.,0.],[300.,30.],[0.,30.]]))}
        self.assertTrue(np.isfinite(self.W).all())
        area=np.linalg.norm(np.cross(self.W[self.T[:,1]]-self.W[self.T[:,0]],self.W[self.T[:,2]]-self.W[self.T[:,0]]),axis=1)
        self.assertTrue((area>0).all())

    def lines(self,state='active',sel=('P',),generation=1):
        shown={'P':(self.F,self.T,self.W,('topology',),('shape',generation))}
        return PK._placed_lines(self.spec,self.out,{'P':state},sel,shown,only={'P'})

    def edges(self,lines):
        return {tuple(sorted(tuple(np.round(q,8)) for q in edge))
                for p,_,_ in lines for edge in p.reshape(-1,2,3)}

    def expected(self):
        E=np.sort(np.vstack([self.T[:,[0,1]],self.T[:,[1,2]],self.T[:,[2,0]]]),axis=1)
        E,c=np.unique(E,axis=0,return_counts=True)
        return {tuple(sorted(tuple(np.round(q,8)) for q in self.W[e])) for e in E[c==1]}

    def test_curved_rim_uses_every_boundary_edge_without_chords(self):
        lines=self.lines()
        self.assertEqual(self.edges(lines),self.expected())
        self.assertEqual(sum(len(p)//2 for p,_,_ in lines),14)

    def test_hole_rim_is_preserved_without_internal_diagonals(self):
        self.F=np.array([[0,0],[3,0],[3,3],[0,3],[1,1],[2,1],[2,2],[1,2]],float)
        self.W=np.c_[self.F,np.zeros(8)]*.01
        self.T=np.array([[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]])
        lines=self.lines()
        self.assertEqual(self.edges(lines),self.expected())
        self.assertEqual(sum(len(p)//2 for p,_,_ in lines),8)

    def test_changed_shape_and_mirrored_half_use_current_world_positions(self):
        self.lines()
        self.W[:,0]*=-1;self.W+=np.array([.2,-.1,.5])
        self.assertEqual(self.edges(self.lines(generation=2)),self.expected())

    def test_unchanged_shape_reuses_cached_boundary(self):
        first=self.lines()
        with patch.object(PK,'_loops',side_effect=AssertionError('warm boundary rebuilt')):
            self.assertIs(self.lines()[0][0],first[0][0])

    def test_unselected_active_has_no_rim_and_freeze_retains_state(self):
        self.assertEqual(self.lines(sel=()),[])
        self.assertEqual({k for _,_,k in self.lines(state='freeze')},{'sel','state'})

    def tearDown(self):PK.invalidate()
