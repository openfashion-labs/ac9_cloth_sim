import copy
import unittest
from solver import native_three_contract as N


class NativeThreeContractTests(unittest.TestCase):
    def test_only_the_intentional_shape_restart_is_expected(self):
        self.assertTrue(N.expected_shape_restart(dict(status='unavailable', reason=N.SHAPE_ONLY_CHECKPOINT)))
        self.assertFalse(N.expected_shape_restart(dict(status='unavailable', reason='Nonfinite state')))
        self.assertFalse(N.expected_shape_restart(dict(status='failed', reason=N.SHAPE_ONLY_CHECKPOINT)))

    def test_configuration_keeps_physics_and_owns_its_result(self):
        cfg = dict(live=True, substeps=10, iterations=20, density=.14,
                   tri_ke=1234., edge_ke=.006, one_layer=[{'piece': 'A'}],
                   bend_fric2={'x': .3}, temp_stitches='tacks.npy')
        before = copy.deepcopy(cfg)
        actual = N.configure(cfg)
        self.assertEqual(cfg, before)
        self.assertEqual(actual, dict(before, native_three=True))
        self.assertIsNot(actual, cfg)

    def test_unsupported_combinations_are_refused_without_mutation(self):
        cases = [dict(fast_live=True), dict(layers={'A': 1}), dict(layer_order=['A']),
                 dict(body_motion='moving.npy'), dict(checkpoint_in='saved.npz'),
                 dict(native_batch_file='external.npy'), dict(native_experimental=True),
                 dict(graph=False), dict(self_contact=False), dict(live=False)]
        for change in cases:
            with self.subTest(change=change):
                cfg = dict(live=True, **{k: v for k, v in change.items() if k != 'live'})
                if 'live' in change:
                    cfg['live'] = change['live']
                before = copy.deepcopy(cfg)
                with self.assertRaises(ValueError):
                    N.configure(cfg)
                self.assertEqual(cfg, before)

    def test_preflight_checks_nested_overrides_and_enabled_piece_layers(self):
        for settings in [dict(fast_live=True), dict(layer_all=['A']), dict(layer={'A': 1}),
                         dict(cfg_override={'s1': {'layers': {'A': 1}}}),
                         dict(cfg_override={'s1': {'native_three': False}}),
                         dict(cfg_override={'s1': {'fast_live': 'accurate'}})]:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                N.preflight({}, settings)
        with self.assertRaises(ValueError):
            N.preflight({'pieces': [{'name': 'A', 'layer': 1}]}, {})
        N.preflight({'pieces': [{'name': 'A', 'layer': 1, 'enabled': False}],
                     'folds': [{'layers': 'A'}]}, {})

    def test_runtime_requirements_are_refused_before_preparation(self):
        for field in ('graph', 'self_contact'):
            for override in ({field: False}, {'s1': {field: False}}):
                settings = dict(native_three=True, cfg_override=override)
                before = copy.deepcopy(settings)
                with self.subTest(override=override), self.assertRaises(ValueError):
                    N.preflight({}, settings)
                self.assertEqual(settings, before)
            # None removes an override; the runtime's default remains enabled.
            for override in ({field: None}, {'s1': {field: None}}):
                settings = dict(native_three=True, cfg_override=override)
                before = copy.deepcopy(settings)
                N.preflight({}, settings)
                self.assertEqual(settings, before)


if __name__ == '__main__':
    unittest.main()
