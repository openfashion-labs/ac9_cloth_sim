"""Start plan (core/start_plan.py): a piece drawn by its moved placed object starts from that shape."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from ac9_cloth_sim.core import start_plan as SP


def _square(off=(0.0, 0.0, 0.0)):
    V = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    T = np.array([[0, 1, 2], [0, 2, 3]])
    X = np.c_[V, np.zeros(4)] + np.asarray(off, float)
    return V, T, X


def _mapper(Q, V, T, X):
    return np.array([X[np.argmin(np.linalg.norm(V - q, axis=1))] for q in Q])


def _src(moved=True, nv=4, made=4, ident=False):
    return {'moved': moved, 'vertex_count': nv, 'made_vertex_count': made, 'vertex_identity': ident}


class Plan(unittest.TestCase):
    def test_sources(self):
        V, T, X = _square()
        screen = {'A': ('placed', (V, T, X), ''), 'B': ('mirrored', (V, T, X), ''), 'C': ('result', None, ''),
                  'D': ('rule', None, ''), 'E': ('placed', None, "its placed object has no layout")}
        pl = SP.plan(screen, ['A', 'B', 'C', 'D', 'E', 'F'], {'A': _src(), 'B': _src()})
        self.assertEqual([pl[n]['source'] for n in 'ABCDEF'],
                         ['placed_object', 'mirrored_object', 'screen_result', 'placement_rule', 'placement_rule',
                          'placement_rule'])
        self.assertEqual(pl['E']['why'], "its placed object has no layout")
        self.assertEqual(pl['F']['why'], "not read before the run")

    def test_not_moved_is_left_to_the_rule(self):
        # the screen is made again when the body or the pattern changes (user 2026-11-13, b-3): not taken
        V, T, X = _square()
        screen = {'A': ('placed', (V, T, X), ''), 'B': ('mirrored', (V, T, X), '')}
        pl = SP.plan(screen, ['A', 'B'], {'A': _src(moved=False), 'B': _src(moved=False)})
        self.assertEqual({pl[n]['source'] for n in 'AB'}, {'placement_rule'})

    def test_vertices_added_by_hand_are_not_taken(self):
        V, T, X = _square()
        screen = {'A': ('placed', (V, T, X), '')}
        pl = SP.plan(screen, ['A'], {'A': _src(nv=5, made=4)})
        self.assertEqual(pl['A'], {'source': 'placement_rule', 'why': "vertices added or deleted by hand"})

    def test_not_read_leaves_everything_to_prepare(self):
        self.assertEqual(SP.plan(None, ['A'])['A']['source'], 'prepare')


class Take(unittest.TestCase):
    def test_a_stale_mirrored_half_takes_the_shape_on_screen(self):
        # the case of 2026-11-13: the mirrored half started from its source's rule shape, 239 mm off
        V, T, X = _square()
        shown = X * [-1, 1, 1]
        screen = {'A': ('placed', (V, T, X), ''), 'B': ('mirrored', (V, T, shown), '')}
        src = {'A': _src(), 'B': _src()}
        pl = SP.plan(screen, ['A', 'B'], src)
        Xs, moved, taken = SP.take(pl, screen, src, [V, V], [X, shown + [0, 239.0, 0]], _mapper)
        np.testing.assert_allclose(Xs[1], shown)
        np.testing.assert_allclose(Xs[0], X)
        self.assertAlmostEqual(moved['B'], 239.0)
        self.assertAlmostEqual(moved['A'], 0.0)
        self.assertEqual(taken, ['A', 'B'])
        ln = SP.lines(pl, moved)
        self.assertEqual(len(ln), 1)
        self.assertIn('WARNING', ln[0])

    def test_a_placed_object_of_another_layout_is_mapped(self):
        V, T, X = _square()
        Vr = V[[2, 0, 3, 1]]
        Xr = X[[2, 0, 3, 1]] + [0, 0, 5.0]
        screen = {'A': ('placed', (Vr, T, Xr), '')}
        pl = SP.plan(screen, ['A'], {'A': _src()})
        Xs, _m, _t = SP.take(pl, screen, {'A': _src()}, [V], [X], _mapper)
        np.testing.assert_allclose(Xs[0], X + [0, 0, 5.0])

    def test_a_mirrored_half_of_another_mesh_is_left(self):
        V, T, X = _square()
        screen = {'B': ('mirrored', (V[:3], T[:1], X[:3]), '')}
        pl = SP.plan(screen, ['B'], {'B': _src(nv=3, made=3)})
        Xs, moved, taken = SP.take(pl, screen, {}, [V], [X + 1.0], _mapper)
        np.testing.assert_allclose(Xs[0], X + 1.0)
        self.assertEqual(pl['B']['source'], 'placement_rule')
        self.assertEqual((moved, taken), ({}, []))

    def test_results_and_rules_are_not_touched(self):
        V, T, X = _square()
        screen = {'C': ('result', (V, T, X + 50.0), ''), 'D': ('rule', None, '')}
        pl = SP.plan(screen, ['C', 'D'])
        Xs, moved, taken = SP.take(pl, screen, {}, [V, V], [X, X], _mapper)
        np.testing.assert_allclose(Xs[0], X)
        self.assertEqual(moved, {})

    def test_drop_names(self):
        self.assertEqual(SP.drop_names("  put back by Reset, from the garment's shape: A, B", {'B'}),
                         "  put back by Reset, from the garment's shape: A")
        self.assertEqual(SP.drop_names("  put back by Reset, from the garment's shape: B", {'B'}), '')


if __name__ == '__main__':
    unittest.main()
