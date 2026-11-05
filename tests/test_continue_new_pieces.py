"""Continue after pieces were added: they are sewn on without gravity first (and so with the stitch cap); without
new pieces the first stage keeps gravity."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import recipe


def _spec():
    return {'pieces': [{'name': 'A'}, {'name': 'B'}, {'name': 'C'}], 'seams': [], 'placement': {}, 'folds': []}


class ContinueNewPieces(unittest.TestCase):
    def _stages(self, new):
        kind, stg = recipe.continue_stages(_spec(), ['A', 'B', 'C'], 'p.npz', [0.0, 1.0, 1.0], new=new)
        recipe.physical(stg, 140.0, 6.0, 6.0, mesh_independent=True)
        return kind, stg

    def test_new_pieces_sewn_without_gravity(self):
        kind, stg = self._stages(['C'])
        self.assertEqual(kind, 'continue')
        first = stg[0][1]
        self.assertEqual(first['gravity_z'], 0.0)
        self.assertIn('stitch_cap_N', first)
        self.assertEqual(first['stitch_ramp_frames'], recipe.SEW_RAMP_FRAMES)
        self.assertTrue(stg[-1][0].startswith('close the seams'))
        self.assertNotIn('gravity_z', stg[-1][1])

    def test_no_new_pieces_keeps_gravity(self):
        _kind, stg = self._stages([])
        self.assertNotIn('gravity_z', stg[0][1])
        self.assertNotIn('stitch_cap_N', stg[0][1])

    def test_live_unchanged(self):
        kind, stg = recipe.continue_stages(_spec(), ['A', 'B', 'C'], 'p.npz', [0.0, 1.0, 1.0], new=['C'], live=True)
        self.assertEqual(kind, 'live')
        self.assertNotIn('gravity_z', stg[0][1])


if __name__ == '__main__':
    unittest.main()
