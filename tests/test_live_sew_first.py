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
        self.assertAlmostEqual(sf['cap_N'], 5.4)          # 0.9 N per mm of mesh edge on 6 mm
        self.assertNotIn('gravity_z', cfg)
        self.assertNotIn('stitch_cap_N', cfg)
        self.assertNotIn('air_damp', cfg)
        self.assertTrue(sf['hold'])              # sewn pieces held still while a piece still floats
        self.assertEqual(sf['hold_stall'], 60)   # ... let go when its stitches stop closing
        self.assertNotIn('heavy', sf)

    def test_hold_outlasts_the_sewing_stall(self):
        """A piece the sewing counts as sewn by its own stall rule (30 frames) stays held through relax_frames: the
        hold's own stall let go (at 30) on the frame the piece was sewn by a stall, 24 frames early."""
        import inspect
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
        from sew_first import PieceSewing
        stall = inspect.signature(PieceSewing).parameters['stall_frames'].default
        sf = recipe.LIVE_SEW_FIRST
        self.assertGreater(sf['hold_stall'], stall + sf['relax_frames'])

    def test_cap_does_not_depend_on_the_fabric(self):
        """The sewing speed is the same for every fabric (user 2026-11-22): a jersey's membrane gave 0.504 N before."""
        caps = []
        for membrane in ((280.0, 280.0, 26.0), (3000.0, 3000.0, 29.0), (15.0, 15.0, 5.0)):
            for new in ((), ['B']):
                kind, stg = recipe.continue_stages(_spec(), ['A', 'B'], 'p.npz', [0.0, 1.0, 1.0], new=new,
                                                   live=not new)
                recipe.physical(stg, 140.0, 6.0, 6.0, mesh_independent=True, membrane=membrane)
                for _n, c in stg:
                    if 'stitch_cap_N' in c:
                        caps.append(c['stitch_cap_N'])
                    if 'sew_first' in c:
                        caps.append(c['sew_first']['cap_N'])
        self.assertTrue(caps)
        for c in caps:
            self.assertAlmostEqual(c, 5.4)
        kind, stg = recipe.continue_stages(_spec(), ['A', 'B'], 'p.npz', [0.0, 1.0, 1.0], live=True)
        recipe.physical(stg, 140.0, 6.0, 4.0, mesh_independent=True, membrane=(280.0, 280.0, 26.0))
        self.assertAlmostEqual(stg[0][1]['sew_first']['cap_N'], 3.6)   # per mm of mesh edge

    def test_table_not_changed_by_a_stage(self):
        _kind, stg = self._live()
        stg[0][1]['sew_first']['gap_max_mm'] = 99.0
        self.assertEqual(recipe.LIVE_SEW_FIRST['gap_max_mm'], 3.0)

    def test_phase_stages_have_no_sew_first(self):
        _kind, stg = recipe.continue_stages(_spec(), ['A', 'B'], 'p.npz', [0.0, 1.0, 1.0], new=['B'])
        self.assertFalse(any('sew_first' in c for _n, c in stg))


if __name__ == '__main__':
    unittest.main()
