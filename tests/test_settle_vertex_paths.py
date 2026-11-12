"""Forward moving-vertex/static-cloth path clipping, with explicit limitations."""
import ast
from pathlib import Path
import unittest
import numpy as np
tree=ast.parse((Path(__file__).resolve().parents[1]/'place_settle.py').read_text(encoding='utf8'))
scope={'np':np}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_step_obstacles','_clip_vertex_steps')],type_ignores=[]),'place_settle.py','exec'),scope)
obstacles,clip=scope['_step_obstacles'],scope['_clip_vertex_steps']

def sheet(z=0.):
    return np.array([[-2.,-2.,z],[2.,-2.,z],[0.,2.,z]]),np.array([[0,1,2]])

class VertexPaths(unittest.TestCase):
    def test_first_face_and_starting_side(self):
        p=np.array([[0.,0.,-1.]]);move=np.array([[0.,0.,2.]])
        for layers in ([sheet(0),sheet(.5)],[sheet(.5),sheet(0)]):
            end=p+clip(p,move,obstacles(layers))
            self.assertAlmostEqual(end[0,2],-1e-4,places=12)

    def test_initial_contact_can_separate_both_directions(self):
        p=np.zeros((1,3))
        for sign in (-1.,1.):
            move=np.array([[0.,0.,sign]])
            np.testing.assert_array_equal(clip(p,move,obstacles([sheet()])),move)

    def test_initial_contact_does_not_hide_a_second_layer(self):
        p=np.zeros((1,3));move=np.array([[0.,0.,1.]])
        for gap in (1e-7,1e-5,.001,.5):
            end=p+clip(p,move,obstacles([sheet(),sheet(gap)]))
            self.assertLess(end[0,2],gap)
            self.assertGreaterEqual(end[0,2],0)

    def test_parallel_and_no_hit_are_exact(self):
        p=np.array([[0.,0.,1.],[4.,0.,-1.]])
        move=np.array([[10.,0.,0.],[0.,0.,2.]])
        np.testing.assert_array_equal(clip(p,move,obstacles([sheet()])),move)

    def test_zero_fixed_and_tiny_steps(self):
        p=np.array([[0.,0.,-1.],[0.,0.,1.],[0.,0.,-1e-12]])
        move=np.array([[0.,0.,0.],[1e-16,0.,0.],[0.,0.,2e-12]])
        got=clip(p,move,obstacles([sheet()]))
        np.testing.assert_array_equal(got[:2],move[:2]);self.assertLess((p+got)[2,2],0)

    def test_contact_at_endpoint_or_triangle_edge(self):
        for xy in ((0.,0.),(-2.,-2.)):
            p=np.array([[*xy,-1.]]);move=np.array([[0.,0.,1.]])
            self.assertLess((p+clip(p,move,obstacles([sheet()])))[0,2],0)

    def test_existing_penetration_is_not_ejected(self):
        p=np.array([[0.,0.,-1.]]);move=np.array([[0.,0.,-.5]])
        np.testing.assert_array_equal(clip(p,move,obstacles([sheet()])),move)

    def test_no_obstacles_and_degenerate_faces(self):
        p=np.array([[0.,0.,-1.]]);move=np.array([[0.,0.,2.]])
        np.testing.assert_array_equal(clip(p,move,obstacles([])),move)
        np.testing.assert_array_equal(clip(p,move,obstacles([(np.zeros((3,3)),np.array([[0,1,2]]))])),move)

    def test_rigid_coordinates_and_long_move(self):
        p=np.array([[0.,0.,-100.]]);move=np.array([[0.,0.,200.]])
        X,T=sheet();angle=.42;c,s=np.cos(angle),np.sin(angle)
        rot=np.array([[c,0.,s],[0.,1.,0.],[-s,0.,c]]);shift=np.array([-60.,-55.,1520.])
        a=clip(p,move,obstacles([(X,T)]))
        b=clip(p@rot.T+shift,move@rot.T,obstacles([(X@rot.T+shift,T)]))
        np.testing.assert_allclose(b,a@rot.T,atol=1e-11,rtol=0)

    def test_reverse_vertex_face_is_explicitly_not_covered(self):
        X=np.array([[-2.,-2.,-1.],[2.,-2.,-1.],[0.,2.,-1.]])
        move=np.tile([0.,0.,2.],(3,1));small=np.array([[-.1,-.1,0],[.1,-.1,0],[0,.1,0]])
        np.testing.assert_array_equal(clip(X,move,obstacles([(small,np.array([[0,1,2]]))])),move)
        # At half-time an obstacle vertex lies inside the moving triangle.
        mid=X+move*.5
        uv=np.linalg.lstsq(np.stack([mid[1]-mid[0],mid[2]-mid[0]],1),small[0]-mid[0],rcond=None)[0]
        self.assertTrue(np.r_[1-uv.sum(),uv].min()>0)

    def test_edge_edge_passage_is_explicitly_not_covered(self):
        X=np.array([[-2.,0.,-2.],[-2.,0.,2.],[-1.,0.,2.]])
        move=np.tile([4.,0.,0.],(3,1));static=np.array([[-1.,-1.,0.],[1.,-1.,0.],[0.,1.,0.]])
        np.testing.assert_array_equal(clip(X,move,obstacles([(static,np.array([[0,1,2]]))])),move)
        # At half-time the moving vertical edge passes through the static face.
        mid=X+move*.5;hit=(mid[0]+mid[1])*.5
        uv=np.linalg.lstsq(np.stack([static[1]-static[0],static[2]-static[0]],1),hit-static[0],rcond=None)[0]
        self.assertTrue(np.r_[1-uv.sum(),uv].min()>0)

if __name__=='__main__':unittest.main()
