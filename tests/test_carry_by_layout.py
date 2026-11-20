"""A shape put on another mesh of the same piece (core/shape_source.carry): vertex by vertex only where the two
layouts are the same point by point, never because of a mark.

The case behind it: a pair's placed object made on an older mesh of the piece; a neighbor piece switched Off then
re-sampled the seam on the outline and the mesh was made again with the same vertex count (3092) and 2986 vertices
renumbered. The mirrored half was taken vertex by vertex and started torn (edges to 623 mm), and the start check, taking
the same shortcut, said 0.0 mm. Every way in here gets the same input: a shape whose vertices are the run's, numbered
otherwise."""
import inspect
import re
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from ac9_cloth_sim.core import shape_source as SH
from ac9_cloth_sim.core import start_check as CS
from ac9_cloth_sim.core import start_plan as SP

ADDON = Path(__file__).resolve().parents[1]


def _grid(n=6, h=10.0):
    """A small piece: an n x n grid of layout points (mm), its triangles, and a curved shape (mm)."""
    ij = np.array([(i, j) for j in range(n) for i in range(n)], float)
    V = ij * h
    T = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            T += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    X = np.c_[V[:, 0], 30.0 * np.sin(V[:, 1] / 25.0), V[:, 1]] + [0, 0, 100.0]
    return V, np.array(T, np.int64), X


def _renumbered(V, T, X, seed=3):
    """The same mesh with its vertices numbered otherwise (same count, same points)."""
    p = np.random.default_rng(seed).permutation(len(V))
    inv = np.argsort(p)
    return V[p], inv[T], X[p]


def _mapper(Q, V, T, X):            # the points are the same set here: the nearest one is exact
    return np.array([X[np.argmin(np.linalg.norm(V - q, axis=1))] for q in Q])


def _longest_ratio(V, T, X):
    """Longest edge of the shape over its pattern length (a torn start is far over 1)."""
    e = np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]
    return float((np.linalg.norm(X[e[:, 0]] - X[e[:, 1]], axis=1)
                  / np.linalg.norm(V[e[:, 0]] - V[e[:, 1]], axis=1)).max())


class Carry(unittest.TestCase):
    def test_same_layout_is_taken_vertex_by_vertex(self):
        V, T, X = _grid()
        Y, same = SH.carry(V, V, T, X, lambda *a: self.fail("mapped"))
        self.assertTrue(same)
        np.testing.assert_allclose(Y, X)

    def test_renumbered_mesh_of_the_same_count_is_mapped(self):
        V, T, X = _grid()
        Vr, Tr, Xr = _renumbered(V, T, X)
        Y, same = SH.carry(V, Vr, Tr, Xr, _mapper)
        self.assertFalse(same)
        np.testing.assert_allclose(Y, X)

    def test_float32_layout_points_are_the_same_layout(self):
        V, T, X = _grid()
        Y, same = SH.carry(V, V.astype(np.float32), T, X, lambda *a: self.fail("mapped"))
        self.assertTrue(same)


class EveryWayIn(unittest.TestCase):
    """The run's layout V and its right start X; on screen the same shape numbered otherwise."""

    def setUp(self):
        self.V, self.T, self.X = _grid()
        self.Vr, self.Tr, self.Xr = _renumbered(self.V, self.T, self.X)

    def test_take_placed_and_mirrored_alike(self):
        for kind in ('placed', 'mirrored'):
            screen = {'A': (kind, (self.Vr, self.Tr, self.Xr), '')}
            src = {'A': {'moved': True, 'vertex_count': len(self.V), 'made_vertex_count': len(self.V)}}
            pl = SP.plan(screen, ['A'], src)
            Xs, _moved, taken = SP.take(pl, screen, src, [self.V], [self.X + 50.0], _mapper)
            self.assertEqual(taken, ['A'], kind)
            np.testing.assert_allclose(Xs[0], self.X, err_msg=kind)
            self.assertAlmostEqual(_longest_ratio(self.V, self.T, Xs[0]), _longest_ratio(self.V, self.T, self.X),
                                   msg=kind)
            self.assertGreater(_longest_ratio(self.V, self.T, self.Xr), 3.0)     # what vertex by vertex would be

    def test_check_finds_a_start_taken_vertex_by_vertex(self):
        # the start check compares through the layouts, so a torn start is said and a right one is not
        flat, offs = self.V, [0]
        for kind in ('placed', 'mirrored'):
            screen = {'A': (kind, (self.Vr, self.Tr, self.Xr), '')}
            right = CS.compare(screen, ['A'], flat, offs, self.X, _mapper)['A']
            self.assertTrue(right['checked'], kind)
            self.assertEqual(right['over'], [], kind)
            torn = CS.compare(screen, ['A'], flat, offs, self.Xr, _mapper)['A']
            self.assertTrue(torn['checked'], kind)
            self.assertGreater(len(torn['over']), len(self.V) // 2, kind)


class NoMarkDecidesTheShortcut(unittest.TestCase):
    """Whether vertex k is vertex k is decided from the layout points in one place (shape_source.carry). A mark
    written when a mesh was made -- the mirrored half's label, the vertex identity flag (ac9_kk), the layout key -- is
    not read to decide it anywhere."""

    def _sources(self):
        for p in sorted(ADDON.rglob('*.py')):
            rel = p.relative_to(ADDON).as_posix()
            if rel.startswith(('tests/', 'solver/', 'vendor/', 'wheels/')):
                continue
            yield rel, p.read_text(encoding='utf-8')

    def test_identity_flag_and_key_are_not_read_to_decide(self):
        bad = []
        pats = [re.compile(r"get\(\s*(?:PLV\.)?KK_PROP"), re.compile(r"vertex_identity"),
                re.compile(r"KEY_PROP\)\s*[!=]="), re.compile(r"[!=]=\s*(?:PLV\.)?layout_key\(")]
        for rel, text in self._sources():
            for i, line in enumerate(text.splitlines(), 1):
                if any(r.search(line) for r in pats):
                    bad.append(f"{rel}:{i}: {line.strip()}")
        self.assertEqual(bad, [])

    def test_the_start_and_its_check_do_not_branch_on_the_mirrored_label(self):
        for fn in (SP.take, CS.compare):
            self.assertNotIn("mirrored", inspect.getsource(fn), fn.__name__)


if __name__ == '__main__':
    unittest.main()
