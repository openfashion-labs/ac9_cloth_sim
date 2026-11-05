"""Phase Sim again from a frame (core/phase_from.py): where a timeline frame falls in the stages, and the stage's
schedule seen from that frame."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import phase_from as P


class Locate(unittest.TestCase):
    STEPS = [70, 75, 60, 75, 100, 31]          # a T-shirt's six stages, 411 steps, 207 samples

    def test_samples_are_every_second_step_and_the_last(self):
        self.assertEqual(P.sample_step(0, 207, 411), 0)
        self.assertEqual(P.sample_step(10, 207, 411), 20)
        self.assertEqual(P.sample_step(205, 207, 411), 410)
        self.assertEqual(P.sample_step(206, 207, 411), 411)
        self.assertEqual(P.sample_step(-1, 207, 411), 0)          # frame 0 (before the cache's first frame)
        self.assertEqual(P.sample_step(500, 207, 411), 411)

    def test_locate(self):
        self.assertEqual(P.locate(self.STEPS, 0), (0, 0))
        self.assertEqual(P.locate(self.STEPS, 69), (0, 69))
        self.assertEqual(P.locate(self.STEPS, 70), (1, 0))        # a stage's last frame = the next one's first
        self.assertEqual(P.locate(self.STEPS, 150), (2, 5))
        self.assertEqual(P.locate(self.STEPS, 411), (6, 0))       # past the last stage: nothing left

    def test_plan_place_of_a_job_that_went_on_from_a_frame(self):
        info = {'phase_from': {'stage': 2, 'frame': 5}}
        self.assertEqual(P.plan_place(info, 0, 10), (2, 15))
        self.assertEqual(P.plan_place(info, 1, 10), (3, 10))
        self.assertEqual(P.plan_place({}, 1, 10), (1, 10))


class Trim(unittest.TestCase):
    CFG = {'frames': 60, 'stitch_ramp_frames': 20, 'lay': {'x': 1}, 'layer_order': [1],
           'stitch_delay': [{'pieces': ['A', 'A'], 'start': 100000, 'frames': 1},
                            {'pieces': ['A', 'B'], 'start': 30, 'frames': 20},
                            {'pieces': ['B', 'C'], 'start': 5, 'frames': 10},
                            {'pieces': ['C', 'D'], 'start': 0, 'frames': 40}],
           'fold_ramp': {'pieces': ['A'], 'frames': 30},
           'elastic': [{'piece': 'A', 'ratio': 0.9, 'start': 1, 'frames': 10}]}

    def test_frame_zero_is_unchanged(self):
        c, notes = P.trim(dict(self.CFG), 0)
        self.assertEqual(c, self.CFG)
        self.assertEqual(notes, [])

    def test_schedules_shift(self):
        c, notes = P.trim(dict(self.CFG), 25)
        self.assertEqual(c['frames'], 35)
        self.assertEqual(c['stitch_ramp_frames'], 0)                # done by frame 20
        d = {tuple(g['pieces']): (g['start'], g['frames']) for g in c['stitch_delay']}
        self.assertEqual(d[('A', 'A')], (100000, 1))                # never opens: kept
        self.assertEqual(d[('A', 'B')], (5, 20))                    # not yet open: 5 frames to go
        self.assertNotIn(('B', 'C'), d)                             # closed by frame 15
        self.assertEqual(d[('C', 'D')], (0, 15))                    # closing: the rest of it
        self.assertEqual(c['fold_ramp']['frames'], 5)
        self.assertEqual(c['elastic'][0]['start'], -24)             # the rest shape's ramp, the same frames
        self.assertNotIn('lay', c)                                  # done when the stage started
        self.assertIn('layer_order', c)                             # held through the stage
        self.assertEqual(notes, ['lay'])
        self.assertEqual(self.CFG['frames'], 60)                    # the plan's config is not changed

    def test_cut(self):
        st = [('one', {'frames': 10}), ('two', {'frames': 20, 'init_from': 'old/s1', 'init_from_pieces': ['A']}),
              ('three', {'frames': 30, 'init_from': 'old/s2'})]
        out, _ = P.cut(st, 1, 4, lambda i: f"new/s{i + 1}")
        self.assertEqual([l for l, _ in out], ['two (from frame 4)', 'three'])
        self.assertNotIn('init_from', out[0][1])
        self.assertNotIn('init_from_pieces', out[0][1])
        self.assertEqual(out[0][1]['frames'], 16)
        self.assertEqual(out[1][1]['init_from'], 'new/s1')
        out, _ = P.cut(st, 2, 0, lambda i: f"new/s{i + 1}")
        self.assertEqual(out, [('three', {'frames': 30})])


if __name__ == '__main__':
    unittest.main()
