"""Persistent piece controls and reversible constitutive updates, without a GPU."""
import ast
import importlib.util
import pathlib
import sys
import unittest
from types import SimpleNamespace

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_strengthen_test'
definition = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(definition)
sys.modules[PACKAGE] = package
definition.loader.exec_module(package)
S = __import__(PACKAGE + '.core.spec', fromlist=['spec'])
recipe = __import__(PACKAGE + '.core.recipe', fromlist=['recipe'])


class Array:
    def __init__(self, value, dtype=np.float32):
        self.value = np.array(value, dtype=dtype)

    def numpy(self):
        return self.value.copy()

    def assign(self, value):
        self.value[:] = value


class Strengthen(unittest.TestCase):
    def test_persistent_controls_and_live_scope(self):
        spec = S.new_spec('test')
        spec['pieces'] = [{'name': 'A', 'strengthen': True, 'strengthen_factor': 7},
                          {'name': 'B', 'strengthen': False, 'strengthen_factor': 9}]
        saved = S.loads(S.dumps(spec))
        self.assertEqual(recipe.live_params(saved, ['A', 'B'], True)['strengthen_pieces'], {'A': 7})
        self.assertEqual(S.strengthen_pieces(saved, ['B']), {})
        self.assertEqual(saved['pieces'][1]['strengthen_factor'], 9)
        self.assertEqual(S.piece_strengthen_factor({}), 5)
        saved['pieces'][0]['strengthen_factor'] = 1
        self.assertEqual(S.strengthen_pieces(saved, ['A']), {'A': 1})

    def test_repeated_edits_preserve_other_materials_and_history_coefficients(self):
        # Exercise the actual worker function with host array adapters; no substitute law.
        source = ast.parse((ROOT / 'solver/newton_vbd.py').read_text(encoding='utf-8'))
        function = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                        and n.name == '_set_strengthen')
        model = SimpleNamespace(tri_materials=Array([[2, 3, 4, 5, 6], [7, 8, 9, 10, 11]]),
                                edge_bending_properties=Array([[2, 3], [0, 0], [5, 6]]),
                                edge_indices=Array([[0, 1, 0, 1], [0, 1, 0, 1], [2, 3, 2, 3]], int))
        state = dict(np=np, model=model, pat_names=['A', 'B'], v_piece=np.array([0, 0, 1, 1]),
                     sel=np.arange(4), d={'fp': np.array([0, 1])}, MEMBRANE_BW=True, NATIVE_COLOR_PLAN=None,
                     STRENGTHEN={}, BF2={'f_on': 1, 'k0': Array([2, 0, 5]), 'r_np': np.arange(3)})
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'worker', 'exec'), state)
        base = model.tri_materials.numpy()
        for factor in (5, 7, 7, 100, 1):
            state['_set_strengthen']({'A': factor})
            current = model.tri_materials.numpy()
            np.testing.assert_array_equal(current[0, [0, 1, 3]], base[0, [0, 1, 3]] * factor)
            np.testing.assert_array_equal(current[1], base[1])
            np.testing.assert_array_equal(current[:, [2, 4]], base[:, [2, 4]])
            np.testing.assert_array_equal(state['BF2']['k0'].numpy(), [2 * factor, 0, 5])
        state['_set_strengthen']({})
        np.testing.assert_array_equal(model.tri_materials.numpy(), base)
        for invalid in (0, -1, 101, np.nan, np.inf):
            with self.assertRaises(ValueError):
                state['_set_strengthen']({'A': invalid})
            np.testing.assert_array_equal(model.tri_materials.numpy(), base)
        state.update(cfg={'experimental_fold_terminal': {'schema': 1}},
                     STRENGTHEN_TERMINAL_DIRTY=False)
        state['_set_strengthen']({'A': 1}, live=True)
        self.assertFalse(state['STRENGTHEN_TERMINAL_DIRTY'])
        state['_set_strengthen']({'A': 7}, live=True)
        self.assertTrue(state['STRENGTHEN_TERMINAL_DIRTY'])
        state['_set_strengthen']({'A': 7}, live=True)
        state['_set_strengthen']({}, live=True)
        self.assertTrue(state['STRENGTHEN_TERMINAL_DIRTY'])

    def test_hold_keeps_the_balance_it_hangs_in_and_gives_the_pattern_back(self):
        # The worker's own functions on host arrays: a square of two triangles, folded along its diagonal and stretched
        # 10 % along x in 3D. The hold moves each rest towards that 3D shape by (1 - 1/factor) -- stiffer around the
        # shape it hangs in, with the same forces then (bending and membrane); a factor change moves the rests again
        # from the same shape; releasing gives back the pattern's poses and the design angle.
        source = ast.parse((ROOT / 'solver/newton_vbd.py').read_text(encoding='utf-8'))
        names = ('_poses_of', '_rest_edges', '_hinge_angles', '_hold_rests', '_hold_update', '_hold_drop_tris')
        funcs = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(len(funcs), len(names))
        flat = np.array([[0., 0.], [10., 0.], [10., 10.], [0., 10.]])           # mm
        F = np.array([[0, 1, 2], [0, 2, 3]])
        q = np.array([[0., 0., 0.], [0.011, 0., 0.], [0.011, 0.01, 0.], [0., 0.01, 0.004]], np.float32)   # m
        Q = np.stack([flat[F[:, 1]] - flat[F[:, 0]], flat[F[:, 2]] - flat[F[:, 0]]], -1) / 1000.0
        poses0 = np.linalg.inv(Q).astype(np.float32)
        # edge 1: a seam hinge across this piece's edge to another piece (vertex 4), stiffened by strengthen too
        q = np.r_[q, [[0.011, -0.01, 0.003]]].astype(np.float32)
        model = SimpleNamespace(tri_poses=Array(poses0), edge_indices=Array([[1, 3, 0, 2], [2, 4, 0, 1]], int),
                                edge_rest_angle=Array([0., 0.]))
        BF2 = {'f_on': 1, 'inside': 0, 'ei': Array([[1, 3, 0, 2]], int), 'hm': Array([0], int), 'hv': Array([0.]),
               'thy': Array([0.3]), 'ra0': Array([0.]), 'st': Array([0.]), 'r_np': np.array([0])}
        log = {}
        state = dict(np=np, model=model, BF2=BF2, WELD=None, FAST_LIVE=False, MEMBRANE_BW=True, log=log,
                     s0=SimpleNamespace(particle_q=Array(q)), v_piece=np.array([0, 0, 0, 0, 1]), sel=np.arange(5),
                     pat_names=['A', 'B'], d={'fp': np.array([0, 0]), 'flat': flat}, F=F,
                     STRENGTHEN={'A': 5.0}, HOLD={}, cfg={})
        exec(compile(ast.Module(body=[n for n in source.body if isinstance(n, ast.FunctionDef)
                                      and n.name == '_hinge_angles'], type_ignores=[]), 'worker', 'exec'), state)
        th0 = float(state['_hinge_angles'](q.astype(np.float64), np.array([[1, 3, 0, 2]]))[0])
        BF2['st'] = Array([th0 - 0.01])                                      # stuck 0.01 rad behind the angle now
        exec(compile(ast.Module(body=funcs, type_ignores=[]), 'worker', 'exec'), state)
        E3 = np.stack([q[F[:, 1]] - q[F[:, 0]], q[F[:, 2]] - q[F[:, 0]]], -1).astype(np.float64)
        G = E3 @ np.linalg.inv(Q)
        w, V = np.linalg.eigh(np.transpose(G, (0, 2, 1)) @ G)
        U = V @ (np.sqrt(w)[..., None] * np.transpose(V, (0, 2, 1)))               # its stretch now, flat frame

        def check(m):
            a = 1.0 - 1.0 / m
            th = float(state['HOLD']['A']['th'][0])
            self.assertGreater(abs(th), 0.3)                                       # it is folded
            self.assertAlmostEqual(float(BF2['hv'].numpy()[0]), th * a, places=5)
            sth = float(state['_hinge_angles'](q.astype(np.float64), np.array([[2, 4, 0, 1]]))[0])
            self.assertGreater(abs(sth), 0.1)
            self.assertAlmostEqual(float(model.edge_rest_angle.numpy()[1]), sth * a, places=5)
            rest = state['_rest_edges'](model.tri_poses.numpy(), np.arange(2))
            # the stretch against the new rest is I + (U - I) / m: m times its strain is the strain it had
            np.testing.assert_allclose(rest, np.linalg.solve(np.eye(2) + (U - np.eye(2)) / m, U @ Q), atol=1e-8)
            S = np.linalg.svd(E3 @ np.linalg.inv(rest), compute_uv=False)
            S0 = np.linalg.svd(E3 @ np.linalg.inv(Q), compute_uv=False)
            np.testing.assert_allclose(m * (S - 1.0), S0 - 1.0, atol=1e-6)

        state['_hold_update'](10)
        self.assertEqual(BF2['hm'].numpy().tolist(), [1])
        check(5.0)
        # the stuck spring is x5 too: its offset is cut to a fifth, so its force stays as it was
        self.assertAlmostEqual(float(BF2['st'].numpy()[0]), th0 - 0.002, places=6)
        self.assertEqual([x[1] for x in log['strengthen_hold']['taken']], ['A'])
        state['STRENGTHEN']['A'] = 20.0                                       # a factor change: same shape, new rests
        state['_hold_update'](11)
        check(20.0)
        self.assertAlmostEqual(float(BF2['st'].numpy()[0]), th0 - 0.0005, places=6)
        self.assertEqual(len(log['strengthen_hold']['taken']), 1)
        self.assertEqual(log['strengthen_hold']['factor'], [[11, 'A', 20.0]])
        state['_hold_drop_tris'](np.array([1]))                              # an elastic sets triangle 1's rest
        self.assertEqual(state['HOLD']['A']['tris'].tolist(), [0])
        state['_hold_drop_tris'](np.array([]))
        state['HOLD']['A']['tris'] = np.arange(2)                             # back for the release check below
        state['HOLD']['A']['poses0'] = poses0.copy()
        state['STRENGTHEN'].clear()                                           # off: the pattern back
        state['_hold_update'](12)
        self.assertEqual(BF2['hm'].numpy().tolist(), [0])
        np.testing.assert_array_equal(model.tri_poses.numpy(), poses0)
        self.assertEqual(float(model.edge_rest_angle.numpy()[1]), 0.0)
        self.assertEqual(log['strengthen_hold']['released'], [[12, 'A']])
        state['STRENGTHEN']['A'] = 5.0
        BF2['f_on'] = None                                                    # hysteresis not on yet: waits
        state['_hold_update'](13)
        self.assertEqual(state['HOLD'], {})
        BF2['f_on'] = 1                                                       # the fold terminal test: never taken
        state['cfg']['experimental_fold_terminal'] = {'schema': 1}
        state['_hold_update'](14)
        self.assertEqual(state['HOLD'], {})
        self.assertEqual(log['strengthen_hold']['off'], 'fold terminal')

    def test_an_angle_the_person_sets_wins_over_the_hold(self):
        # A seam folded on purpose (rest != 0) is not held, and a fold line whose angle changes during Live leaves
        # the hold, so the set angle drives it as it does on fold lines that already had one.
        source = ast.parse((ROOT / 'solver/newton_vbd.py').read_text(encoding='utf-8'))
        names = ('_poses_of', '_rest_edges', '_hinge_angles', '_hold_rests', '_hold_update', '_hold_drop')
        funcs = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(len(funcs), len(names))
        flat = np.array([[0., 0.], [10., 0.], [10., 10.], [0., 10.]])
        F = np.array([[0, 1, 2], [0, 2, 3]])
        q = np.array([[0., 0., 0.], [0.011, 0., 0.], [0.011, 0.01, 0.], [0., 0.01, 0.004], [0.011, -0.01, 0.003]],
                     np.float32)
        Q = np.stack([flat[F[:, 1]] - flat[F[:, 0]], flat[F[:, 2]] - flat[F[:, 0]]], -1) / 1000.0
        model = SimpleNamespace(tri_poses=Array(np.linalg.inv(Q)), edge_indices=Array([[1, 3, 0, 2], [2, 4, 0, 1]], int),
                                edge_rest_angle=Array([0., 1.2]), edge_count=2)
        BF2 = {'f_on': 1, 'inside': 0, 'ei': Array([[1, 3, 0, 2]], int), 'hm': Array([0], int), 'hv': Array([0.]),
               'thy': Array([0.3]), 'ra0': Array([0.]), 'st': Array([0.]), 'r_np': np.array([0])}
        state = dict(np=np, model=model, BF2=BF2, WELD=None, FAST_LIVE=False, MEMBRANE_BW=True, log={},
                     s0=SimpleNamespace(particle_q=Array(q)), v_piece=np.array([0, 0, 0, 0, 1]), sel=np.arange(5),
                     pat_names=['A', 'B'], d={'fp': np.array([0, 0]), 'flat': flat}, F=F,
                     STRENGTHEN={'A': 20.0}, HOLD={}, LIVEP={'bf2_row': np.array([0, -1])}, cfg={})
        exec(compile(ast.Module(body=funcs, type_ignores=[]), 'worker', 'exec'), state)
        state['_hold_update'](10)
        self.assertEqual(state['HOLD']['A']['srows'].tolist(), [])          # the folded seam keeps its set angle
        self.assertAlmostEqual(float(model.edge_rest_angle.numpy()[1]), 1.2, places=6)
        self.assertEqual(BF2['hm'].numpy().tolist(), [1])
        state['_hold_drop'](np.array([0]))                                   # a fold ramp on hinge 0 during Live
        self.assertEqual(BF2['hm'].numpy().tolist(), [0])
        self.assertEqual(state['HOLD']['A']['rows'].tolist(), [])
        state['STRENGTHEN']['A'] = 5.0                                      # a later factor change leaves it out
        state['_hold_update'](11)
        self.assertEqual(BF2['hm'].numpy().tolist(), [0])

if __name__ == '__main__':
    unittest.main()
