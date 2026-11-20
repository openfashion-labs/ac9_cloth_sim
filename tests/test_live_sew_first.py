"""Live sews without gravity first: the Live stage carries sew_first (gap, damping, frame cap) and physical() adds the
stitch cap of the zero-gravity stages to it; the stage itself keeps the gravity asked for (the solver holds it back
until the stitches close) and gets no stitch_cap_N of its own (Accurate refuses one)."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import recipe


def _spec():
    return {'pieces': [{'name': 'A'}, {'name': 'B'}], 'seams': [], 'placement': {}, 'folds': []}


class LiveSewFirst(unittest.TestCase):
    def _live(self):
        kind, stg = recipe.continue_stages(_spec(), ['A', 'B'], 'p.npz', [0.0, 1.0, 1.0], live=True)
        recipe.physical(stg, 140.0, 6.0, 6.0, mesh_independent=True)
        return kind, stg

    def test_live_stage_carries_sew_first(self):
        kind, stg = self._live()
        self.assertEqual(kind, 'live')
        cfg = stg[0][1]
        sf = cfg['sew_first']
        for k, v in recipe.LIVE_SEW_FIRST.items():
            self.assertEqual(sf[k], v)
        self.assertAlmostEqual(sf['cap_N'], (cfg['tri_ka'] + 2.0 * cfg['tri_ke']) * 0.1 * 6.0e-3)
        self.assertNotIn('gravity_z', cfg)
        self.assertNotIn('stitch_cap_N', cfg)
        self.assertNotIn('air_damp', cfg)
        self.assertEqual(sf['heavy'], 10)        # sewn pieces weigh 10x while a piece still floats

    def test_table_not_changed_by_a_stage(self):
        _kind, stg = self._live()
        stg[0][1]['sew_first']['gap_max_mm'] = 99.0
        self.assertEqual(recipe.LIVE_SEW_FIRST['gap_max_mm'], 3.0)

    def test_phase_stages_have_no_sew_first(self):
        _kind, stg = recipe.continue_stages(_spec(), ['A', 'B'], 'p.npz', [0.0, 1.0, 1.0], new=['B'])
        self.assertFalse(any('sew_first' in c for _n, c in stg))


if __name__ == '__main__':
    unittest.main()
