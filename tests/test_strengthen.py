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
                     sel=np.arange(4), d={'fp': np.array([0, 1])}, MEMBRANE_BW=True,
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


if __name__ == '__main__':
    unittest.main()
