"""Hidden in 3D only (piece_hide): an eye apart from the four states, never a fifth state."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim import display_state as DS
from ac9_cloth_sim.core import spec as S


class HiddenIsNotAState(unittest.TestCase):
    def test_the_eye_keeps_the_state_and_the_source(self):
        for state in ('active', 'freeze', 'off_pattern', 'off_all'):
            with self.subTest(state=state):
                pc = {'name': 'A'}
                S.set_piece_state(pc, state)
                spec = {'pieces': [pc], 'placement': {'A': {}}}
                seen = DS.plan(spec, result=['A'])['A']
                hid = DS.plan(spec, result=['A'], hidden=['A'])['A']
                # the simulation's view of the piece is the same: only the 'hidden' flag differs
                self.assertEqual({k: v for k, v in hid.items() if k != 'hidden'},
                                 {k: v for k, v in seen.items() if k != 'hidden'})
                self.assertFalse(seen['hidden'])
                # an Off piece is out of the simulation and has no eye
                self.assertEqual(hid['hidden'], state != 'off_all')

    def test_off_all_has_a_cross_not_the_eye(self):
        import bpy  # noqa: F401  (ui needs Blender)
        from ac9_cloth_sim import ui
        self.assertNotIn(ui.STATE_ICON['off_all'], ('HIDE_ON', 'HIDE_OFF'))


if __name__ == '__main__':
    unittest.main()
