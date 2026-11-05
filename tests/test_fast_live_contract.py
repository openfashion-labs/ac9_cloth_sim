"""Fast is opt-in; incompatible input must fail without changing the original config."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solver import fast_live_contract as F

# the Accurate features (on by default) turned off: the refusals of the first release
OFF = {'AC9_TEST_FAST_LAYERS': '0', 'AC9_TEST_FAST_E5': '0', 'AC9_TEST_FAST_E6': '0', 'AC9_TEST_FAST_SELF_CONTACT': '0'}


class FastLiveContractTests(unittest.TestCase):
    def test_explicit_preset_preserves_fabric_and_input(self):
        original = {'fps': 60, 'live': True, 'density': .14, 'tri_ke': 1234.,
                    'tri_ka': 4321., 'tri_ks': 29., 'mi': {'D': 6e-6},
                    'self_contact': True, 'bend_fric2': {'ratio': 100}}
        snapshot = copy.deepcopy(original)
        configured = F.configure(original)
        self.assertEqual(original, snapshot)
        for key in ('density', 'tri_ke', 'tri_ka', 'tri_ks', 'mi'):
            self.assertEqual(configured[key], original[key])
        self.assertEqual(configured['body_kd'], 0.)
        F.validate_config(configured)

    def test_zero_body_damping_is_an_explicit_value(self):
        for kd in (0., 100.):
            configured = F.configure({'fps': 60, 'live': True, 'body_kd': kd})
            self.assertEqual(configured['body_kd'], kd)
            self.assertEqual(configured['fast_live_contract']['body_kd'], kd)
            F.validate_config(configured)

    @mock.patch.dict(os.environ, OFF)
    def test_final_values_cannot_bypass_contract(self):
        configured = F.configure({'fps': 60, 'live': True})
        for key, value in [('substeps', 10), ('iterations', 100), ('body_kd', 100.),
                           ('live', False), ('layers', {'A': 1}), ('grab_mode', 'rigid'),
                           ('fast_live', 'unknown'), ('fric_anchor', False)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                F.validate_config(dict(configured, **{key: value}))

    def test_invalid_contact_coefficients_rejected(self):
        for key in ('body_kd', 'soft_contact_kd', 'body_mu', 'soft_contact_mu'):
            for value in (-1., float('nan'), float('inf')):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    F.configure({'live': True, key: value})

    @mock.patch.dict(os.environ, OFF)
    def test_unsupported_spec_and_overrides_fail_before_mutation(self):
        cases = [({'pieces': [{'name': 'A', 'layer': 1}]}, {}),
                 ({'folds': [{'layers': 'A'}]}, {}),
                 ({}, {'fold_bake': ['A|fold']}),
                 ({}, {'cfg_override': {'s1': {'self_contact': True}}}),
                 ({}, {'cfg_override': {'fast_live': 'unknown'}}),
                 ({}, {'cfg_override': {'fast_live_contract': {}}}),
                 ({}, {'cfg_override': {'grab_mode': 'rigid'}})]
        for spec, settings in cases:
            before = copy.deepcopy((spec, settings))
            with self.subTest(spec=spec, settings=settings), self.assertRaises(ValueError):
                F.preflight(spec, settings)
            self.assertEqual((spec, settings), before)

    @mock.patch.dict(os.environ, OFF)
    def test_messages_name_the_field_and_the_source(self):
        cases = [({'pieces': [{'name': 'Front', 'layer': 1}, {'name': 'Back', 'layer': 0}]}, {},
                  ['Layer (layer order, AC9 Piece)', 'Front', 'Set it to 0']),
                 ({'folds': [{'piece': 'Placket', 'line': 'I2', 'layers': 'B'}]}, {},
                  ['fold Layers (AC9 Internal Line)', 'Placket I2', 'Set it to Both']),
                 ({}, {'layer_all': True}, ['layer order', 'layer_all']),
                 ({}, {'fold_bake': ['Back|I1']}, ['fold_bake', 'Back I1'])]
        for spec, settings, parts in cases:
            with self.subTest(parts=parts), self.assertRaises(ValueError) as caught:
                F.preflight(spec, settings)
            message = str(caught.exception)
            for part in parts + ['turn off AC9 Fast Live']:
                self.assertIn(part, message)
            self.assertNotIn('Back,', message)

    @mock.patch.dict(os.environ, OFF)
    def test_live_update_rejects_whole_profile_before_application(self):
        current = {'gravity': True, 'fold': {}, 'elastic': {}, 'terminal': None}
        before = copy.deepcopy(current)
        with self.assertRaises(ValueError):
            F.validate_live_update({'gravity': False, 'fold': {'A|fold': 90}}, current)
        self.assertEqual(current, before)
        F.validate_live_update({'gravity': False, 'seams_off': ['a'], 'frozen': ['A']}, current)

    @mock.patch.dict(os.environ, OFF)
    def test_live_update_compares_with_the_solver_live_state(self):
        terminal = {'lines': {'Sleeves|L601': {'taper_mm': 48.0}}}
        current = {'fold': {'Sleeves|L601': -180.0}, 'terminal': terminal, 'elastic': {'Cuff': .9}}
        same = {'fold': {'Sleeves|L601': -180.0}, 'fold_terminal': copy.deepcopy(terminal),
                'elastic': {'Cuff': .9}, 'gravity': True}
        F.validate_live_update(same, current)
        F.validate_live_update({'fold_terminal': None, 'fold': {}, 'elastic': {}},
                               {'fold': {}, 'terminal': None, 'elastic': {}})
        for key, value, field in [('fold_terminal', {'lines': {'Sleeves|L601': {'taper_mm': 20.0}}}, 'End Taper'),
                                  ('fold', {'Sleeves|L601': 90.0}, 'Fold'),
                                  ('elastic', {'Cuff': .8}, 'Elastic')]:
            with self.subTest(key=key), self.assertRaises(ValueError) as caught:
                F.validate_live_update(dict(same, **{key: value}), current)
            self.assertIn(field, str(caught.exception))

    @mock.patch.dict(os.environ, OFF)
    def test_through_grab_is_rejected(self):
        F.validate_grab({'verts': [0], 'target': [0, 0, 0]})
        with self.assertRaises(ValueError):
            F.validate_grab({'verts': [0], 'target': [0, 0, 0], 'through': True})


    @mock.patch.dict(os.environ, {})
    def test_accurate_features_on_by_default(self):
        for name in OFF:
            os.environ.pop(name, None)
        # the piece Layer and the fold Layers pass the preflight; layer orders from a preset and fold_bake still stop
        F.preflight({'pieces': [{'name': 'Front', 'layer': 1}], 'folds': [{'piece': 'Placket', 'line': 'I2', 'layers': 'B'}]}, {})
        for settings in ({'layer_all': True}, {'fold_bake': ['Back|I1']}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                F.preflight({}, settings)
        configured = F.configure({'fps': 60, 'live': True, 'self_contact': True, 'one_layer': {'A': 1},
                                  'ghost_pieces': ['B'], 'attach': {'g': 0}})
        self.assertTrue(configured['self_contact'])
        self.assertEqual(configured['fast_live_contract']['linear_tol'], 1e-3)
        F.validate_config(configured)
        for key in ('mirror_map', 'deep_push', 'weld'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                F.configure({'fps': 60, 'live': True, key: {'x': 1}})
        F.validate_live_update({'fold': {'A|fold': 90}, 'elastic': {'Cuff': .8}}, {'fold': {}, 'elastic': {}, 'terminal': None})
        F.validate_grab({'verts': [0], 'target': [0, 0, 0], 'through': True})


if __name__ == '__main__':
    unittest.main()
