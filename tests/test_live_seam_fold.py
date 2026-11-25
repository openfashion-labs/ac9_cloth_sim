"""A seam's fold angle / direction while Live runs (user 2026-11-22), without a GPU: what the add-on writes to
params.json (recipe.live_params) and the solver's own _live_apply / _live_params on host arrays."""
import ast
import importlib.util
import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_live_seam_fold_test'
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


def _seam(sid, a, b, enabled=True):
    return {'id': sid, 'enabled': enabled, 'a': {'piece': a, 'from': ['e0', 0.0], 'to': ['e0', 1.0], 'dir': 1},
            'b': {'piece': b, 'from': ['e0', 0.0], 'to': ['e0', 1.0], 'dir': 1}}


def _worker(names, **state):
    source = ast.parse((ROOT / 'solver/newton_vbd.py').read_text(encoding='utf-8'))
    body = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
    state.setdefault('np', np)
    state.setdefault('os', __import__('os'))
    exec(compile(ast.Module(body=body, type_ignores=[]), 'worker', 'exec'), state)
    return state


def _model(rest):
    model = type('M', (), {})()
    model.edge_rest_angle = Array(rest)
    return model


class LiveSeamFold(unittest.TestCase):
    def test_live_params_carry_seam_folds(self):
        spec = S.new_spec('test')
        spec['pieces'] = [{'name': n} for n in ('A', 'B', 'C')]
        spec['seams'] = [_seam('s1', 'A', 'B'), _seam('s2', 'B', 'C', enabled=False), _seam('s3', 'A', 'C')]
        spec['folds'] = [{'seam': 's1', 'deg': 90.0, 'turn': 'B'}, {'seam': 's2', 'deg': -40.0, 'turn': 'C'},
                         {'seam': 's3', 'deg': 0.0}]
        p = recipe.live_params(spec, ['A', 'B', 'C'], True)
        # the solver's sign (seam_fold_deg: rest = -value); a seam let go keeps its fold, a zero angle is no fold
        self.assertEqual(p['seam_fold'], {'s1': -90.0, 's2': 40.0})
        self.assertEqual(p['seam_turn'], {'s1': 'B', 's2': 'C'})
        # a Turns left to the default is not sent (it follows the other seams: the solver keeps the run's piece)
        spec['folds'][2] = {'seam': 's3', 'deg': 30.0}
        from unittest import mock
        with mock.patch.object(S, 'turn_default', lambda spec_, sm_: sm_['a']['piece']):   # no outlines here
            p = recipe.live_params(spec, ['A', 'B', 'C'], True)
        self.assertEqual(p['seam_fold']['s3'], -30.0)
        self.assertNotIn('s3', p['seam_turn'])
        spec['folds'][2] = {'seam': 's3', 'deg': 0.0}
        # the start config has the same numbers, for the seams switched on
        self.assertEqual(recipe.seam_fold_deg(spec, ['A', 'B', 'C']), {'s1': -90.0})
        # a piece outside the run: its seams are not sent
        self.assertEqual(recipe.live_params(spec, ['A', 'B'], True)['seam_fold'], {'s1': -90.0})

    def test_solver_eases_the_folded_seams_hinges(self):
        # the start's rest = -value (solver sign): Mountain 90 = value -90 = rest +90 deg
        model = _model([0.1, 0.2, np.radians(90.0), np.radians(90.0), 0.0])
        dropped = []
        st = _worker({'_live_apply', '_live_rest', '_live_params'}, model=model, FAST_LIVE=False, BF2=None, log={},
                     LIVE_RAMP=30, SEAM_PAIR_IDX={'seam:s1': [2, 3]}, SEAM_TURN={'s1': 'B'},
                     SEAM_SET_DEG={'seam:s1': 90.0}, _hold_drop=lambda ids: dropped.append(list(ids)),
                     LIVEP={'fold': {}, 'seam_fold': {'s1': -90.0}, 'ramps': [], 'applied': [], 'mt': None,
                            'path': str(ROOT / 'no_such_params.json')})
        apply = st['_live_apply']

        def ease(f0, n=30):
            for f in range(f0, f0 + n):
                st['_live_params'](f)         # no params.json: the running ramps only

        # Mountain 90 -> Valley 45 on the folded seam: its two hinges only, eased over 30 frames to rest = -45 deg
        self.assertEqual(apply({'seam_fold': {'s1': 45.0}, 'seam_turn': {'s1': 'B'}}, 100),
                         ['seam s1 fold 90->-45 deg (2 hinges)'])
        self.assertEqual(dropped, [[2, 3]])  # strengthen's hold lets them go
        ease(100, 15)
        mid = model.edge_rest_angle.numpy()[2]
        self.assertTrue(-np.radians(45.0) < mid < np.radians(90.0))
        ease(115, 15)
        np.testing.assert_allclose(model.edge_rest_angle.numpy(), [0.1, 0.2, -np.radians(45.0), -np.radians(45.0), 0.0],
                                   atol=1e-6)
        self.assertEqual(st['SEAM_SET_DEG']['seam:s1'], -45.0)
        self.assertEqual(st['LIVEP']['ramps'], [])
        # the same values again: nothing
        self.assertEqual(apply({'seam_fold': {'s1': 45.0}, 'seam_turn': {'s1': 'B'}}, 140), [])
        # unfolded (the key gone = 0): back to straight
        self.assertEqual(apply({'seam_fold': {}, 'seam_turn': {}}, 150), ['seam s1 fold -45->0 deg (2 hinges)'])
        ease(150)
        np.testing.assert_allclose(model.edge_rest_angle.numpy()[2:4], [0.0, 0.0], atol=1e-6)

    def test_seams_it_cannot_take_wait_for_the_next_run(self):
        model = _model([0.0, np.radians(30.0)])
        st = _worker({'_live_apply', '_live_rest'}, model=model, FAST_LIVE=False, BF2=None, log={}, LIVE_RAMP=30,
                     SEAM_PAIR_IDX={'seam:s1': [1]}, SEAM_TURN={'s1': 'B'}, SEAM_SET_DEG={}, _hold_drop=lambda ids: None,
                     LIVEP={'fold': {}, 'seam_fold': {'s1': -30.0}, 'ramps': [], 'applied': [], 'gravity': False})
        apply = st['_live_apply']
        # the first params.json, as the run starts: s3 folded but switched off then (no hinges) is where it stands
        self.assertEqual(apply({'seam_fold': {'s1': -30.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}}, 1), [])
        self.assertEqual(apply({'seam_fold': {'s1': -30.0, 's3': -70.0}, 'seam_turn': {'s1': 'B'}}, 2),
                         ['seam s3 fold 70 deg: at the next run (no fold hinges in this run)'])
        self.assertEqual(apply({'seam_fold': {'s1': -30.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}}, 3), [])
        # s2 was not folded when the run began (no fold hinges); s1's Turns changed: each said once, nothing moves
        P = {'seam_fold': {'s1': -60.0, 's2': -20.0, 's3': -50.0}, 'seam_turn': {'s1': 'A', 's2': 'C'}}
        self.assertEqual(apply(P, 10), ['seam s1 fold 60 deg: at the next run (Turns changed)',
                                        'seam s2 fold 20 deg: at the next run (no fold hinges in this run)'])
        self.assertEqual(apply(dict(P, gravity=False), 11), [])
        self.assertEqual(st['LIVEP']['ramps'], [])
        np.testing.assert_allclose(model.edge_rest_angle.numpy(), [0.0, np.radians(30.0)], atol=1e-6)
        # Turns back to the piece the hinges are measured from: s1 eases to its new angle
        self.assertEqual(apply({'seam_fold': {'s1': -60.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}}, 12),
                         ['seam s1 fold 30->60 deg (1 hinges)'])
        self.assertEqual(len(st['LIVEP']['ramps']), 1)

    def test_a_seam_angle_changed_leaves_strengthens_hold(self):
        # the hold (strengthen) keeps a piece's seam hinges at their angle; an angle the person sets wins over it
        model = _model([0.0, 0.0, np.radians(90.0), np.radians(90.0)])
        hold = {'A': {'rows': np.array([0]), 'th': np.zeros(1), 'ra': np.zeros(1),
                      'srows': np.array([1, 2, 3]), 'sth': np.zeros(3), 'sra': np.zeros(3)}}
        st = _worker({'_live_apply', '_live_rest', '_hold_drop'}, model=model, FAST_LIVE=False, log={},
                     LIVE_RAMP=30, SEAM_PAIR_IDX={'seam:s1': [2, 3]}, SEAM_TURN={'s1': 'B'}, SEAM_SET_DEG={},
                     HOLD=hold, BF2={'hm': Array([1, 0, 0, 0], int), 'f_on': None},
                     LIVEP={'fold': {}, 'seam_fold': {'s1': -90.0}, 'ramps': [], 'applied': [],
                            'bf2_row': np.array([0, -1, -1, -1])})
        st['_live_apply']({'seam_fold': {'s1': -60.0}, 'seam_turn': {'s1': 'B'}}, 5)
        np.testing.assert_array_equal(hold['A']['srows'], [1])     # the other seam's hinge stays held
        np.testing.assert_array_equal(hold['A']['rows'], [0])


class LiveSeamFoldWarning(unittest.TestCase):
    def test_a_change_this_run_cannot_take_is_warned_once(self):
        # the add-on's warning (user 2026-11-22: a seam not folded when the run began waits for the next run)
        source = ast.parse((ROOT / 'dress.py').read_text(encoding='utf-8'))
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'AC9_OT_SimDress')
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_seam_fold_wait')
        ns = {'tx': lambda text, *a: text.format(*a)}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'dress', 'exec'), ns)
        said = []
        op = type('Op', (), {'_seam_base': None, '_seam_said': None,
                              'live_seam_folds': staticmethod(lambda: frozenset({'s1'})),
                              'report': lambda self, kind, text: said.append(text)})()
        wait = lambda p: ns['_seam_fold_wait'](op, p)
        wait({'seam_fold': {'s1': -90.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}})      # as the run starts
        wait({'seam_fold': {'s1': 45.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}})       # s1 was folded: no warning
        self.assertEqual(said, [])
        wait({'seam_fold': {'s1': 45.0, 's2': -30.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}})   # s2 from 0
        wait({'seam_fold': {'s1': 30.0, 's2': -30.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}})   # said once
        self.assertEqual(len(said), 1)
        self.assertIn('s2', said[0])
        wait({'seam_fold': {'s1': 30.0, 's2': -30.0, 's3': -70.0}, 'seam_turn': {'s1': 'A'}})   # s3 off, s1 Turns
        self.assertIn('s1, s2, s3', said[-1])
        wait({'seam_fold': {'s1': 30.0, 's3': -50.0}, 'seam_turn': {'s1': 'B'}})       # all back: quiet again
        self.assertEqual(len(said), 2)


if __name__ == '__main__':
    unittest.main()
