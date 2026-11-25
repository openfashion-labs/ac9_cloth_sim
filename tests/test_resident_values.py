import ast,sys,unittest,inspect,textwrap,functools
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'solver'))
import resident_values as V

class Values(unittest.TestCase):
    def test_wrapped_physics_method_is_not_unwrapped_for_certification(self):
        class Builder:
            def add_triangles(self):return 1
            def add_edges(self):return 2
        expected={name:V.semantic_hash(ast.parse(textwrap.dedent(inspect.getsource(getattr(Builder,name)))).body[0]) for name in V.EXPECTED}
        with patch.object(V,'EXPECTED',expected):
            self.assertTrue(V.builder_identity(Builder)[0])
            original=Builder.add_edges
            @functools.wraps(original)
            def changed(self):return original(self)+1
            Builder.add_edges=changed
            self.assertFalse(V.builder_identity(Builder)[0])
    def test_known_right_triangle_and_boundary_edge(self):
        p=np.array([[0,0,0],[1,0,0],[0,1,0]],np.float32)
        areas,poses,angles,lengths=V.geometry_values(p,np.array([[0,1,2]]),np.array([[-1,-1,0,1]]))
        np.testing.assert_array_equal(areas,[.5]);np.testing.assert_array_equal(poses,[np.eye(2)])
        np.testing.assert_array_equal(angles,[0.]);np.testing.assert_array_equal(lengths,[1.])

    def test_degenerate_shape_does_not_return_partial_values(self):
        with self.assertRaisesRegex(RuntimeError,'degenerate'):
            V.geometry_values(np.zeros((3,3)),np.array([[0,1,2]]),np.empty((0,4),int))

    def test_guard_ignores_only_documentation_and_detects_executable_change(self):
        def hash(code):return V.semantic_hash(ast.parse(code).body[0])
        a=hash('def f(x):\n    "documentation"\n    return x + 1\n')
        self.assertEqual(a,hash('def f(x):\n    "different documentation"\n    return x + 1\n'))
        self.assertNotEqual(a,hash('def f(x):\n    "documentation"\n    return x + 2\n'))
        self.assertNotEqual(a,hash('def f(x, y=2):\n    return x + 1\n'))

    def test_source_unavailable_fails_closed(self):
        class Builder:
            add_triangles=len
            add_edges=len
        self.assertFalse(V.builder_identity(Builder)[0])

if __name__=='__main__':unittest.main()
