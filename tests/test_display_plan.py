"""Acceptance table: simulation state must not choose the shape source."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim import display_state as DS
from ac9_cloth_sim.core import spec as S


class DisplayPlanContract(unittest.TestCase):
    def test_states_and_sources_are_independent(self):
        cases = [
            ({'result': ['A']}, 'result'),
            ({'result': ['A'], 'placed': ['A']}, 'result'),
            ({'result': ['A'], 'placed': ['A'], 'reset': ['A']}, 'placed'),
            ({'placed': ['A']}, 'placed'),
            ({}, 'proxy'),
            ({'unplaced': True}, 'none'),
        ]
        for state in ('active', 'freeze', 'off_pattern', 'off_all'):
            for options, expected_source in cases:
                with self.subTest(state=state, source=expected_source):
                    pc = {'name': 'A'}
                    S.set_piece_state(pc, state)
                    spec = {'pieces': [pc], 'placement': {} if options.get('unplaced') else {'A': {}}}
                    p = DS.plan(spec, **{k: v for k, v in options.items() if k != 'unplaced'})['A']
                    self.assertEqual(p['source'], expected_source)
                    self.assertEqual(p['solid'], state in ('active', 'freeze'))
                    self.assertEqual(p['dashed'], state in ('off_pattern', 'off_all'))
                    self.assertEqual(p['seams'], state != 'off_all')
                    self.assertEqual(p['missing'], state in ('active', 'freeze') and expected_source in ('proxy', 'none'))

    def test_removed_pattern_never_leaves_an_active_result_surface(self):
        p = DS.plan({'pieces': [], 'placement': {}}, result=['deleted'])['deleted']
        self.assertEqual(p['source'], 'result')
        self.assertFalse(p['solid'])
        self.assertFalse(p['seams'])
        self.assertTrue(p['dashed'])


if __name__ == '__main__':
    unittest.main()
