"""Freeze Garment While Sewing (solver/sew_first.GarmentHold): while a re-placed piece floats, the sewn pieces are
held; they are let go once it is sewn, or when its stitches stop closing; the pieces the user froze are never held nor
let go by it; nothing is held when no piece floats."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
from sew_first import GarmentHold

# pieces 0, 1 = the sewn garment, 2 = a collar placed again; two particles each, two more not in a piece (the body)
VP = np.array([0, 0, 1, 1, 2, 2, -1, -1])
PIM = np.ones(8)
NONE = np.zeros(3, bool)


def _apply(pim, ch):
    held, on, back = ch
    pim = pim.copy()
    pim[on] = 0.0
    pim[back] = 1.0
    return pim


class GarmentHoldTest(unittest.TestCase):
    def test_sewn_pieces_held_while_a_piece_floats_then_let_go(self):
        h = GarmentHold(8)
        floating = np.array([False, False, True])
        held, on, back = h.update(1, floating, NONE, VP, PIM, None)
        self.assertEqual(held.tolist(), [True, True, False])
        self.assertEqual(on.tolist(), [True, True, True, True, False, False, False, False])
        self.assertFalse(back.any())
        pim = _apply(PIM, (held, on, back))
        self.assertIsNone(h.update(2, floating, NONE, VP, pim, None))          # no change: nothing to write
        held, on, back = h.update(3, NONE, NONE, VP, pim, None)                 # the collar is sewn
        self.assertFalse(held.any())
        self.assertFalse(on.any())
        self.assertEqual(back.tolist(), [True, True, True, True, False, False, False, False])

    def test_nothing_held_when_nothing_floats(self):
        h = GarmentHold(8)
        held, on, back = h.update(1, NONE, NONE, VP, PIM, None)
        self.assertFalse(on.any() or back.any())

    def test_a_piece_the_user_froze_is_not_taken_over(self):
        h = GarmentHold(8)
        floating = np.array([False, False, True])
        frozen = np.array([False, True, False])
        pim = PIM.copy(); pim[2:4] = 0.0                                      # the user's Freeze on piece 1
        held, on, back = h.update(1, floating, frozen, VP, pim, None)
        self.assertEqual(held.tolist(), [True, False, False])
        self.assertEqual(on.tolist(), [True, True, False, False, False, False, False, False])
        pim = _apply(pim, (held, on, back))
        held, on, back = h.update(2, NONE, frozen, VP, pim, None)
        self.assertEqual(back.tolist(), [True, True, False, False, False, False, False, False])   # piece 1 stays

    def test_frozen_by_the_user_while_held_stays_frozen(self):
        h = GarmentHold(8)
        floating = np.array([False, False, True])
        pim = _apply(PIM, h.update(1, floating, NONE, VP, PIM, None))
        frozen = np.array([True, False, False])
        held, on, back = h.update(2, floating, frozen, VP, pim, None)
        self.assertEqual(held.tolist(), [False, True, False])
        self.assertFalse(back[:2].any())                                      # not let go: the user's Freeze

    def test_let_go_when_the_stitches_stop_closing(self):
        h = GarmentHold(8, stall_frames=60, stall_mm=0.5)
        floating = np.array([False, False, True])
        pim = PIM
        # closing 0.2 mm a frame until frame 100, then stuck near 20 mm: let go 60 frames into the stall
        gap = lambda f_: 40.0 - 0.2 * f_ if f_ <= 100 else 20.0 - 0.001 * f_
        let_go = None
        for f_ in range(1, 300):
            ch = h.update(f_, floating, NONE, VP, pim, gap(f_))
            if ch is not None:
                pim = _apply(pim, ch)
                if f_ > 1:
                    let_go = (f_, ch)
                    break
        self.assertIsNotNone(let_go)
        f_, (held, on, back) = let_go
        self.assertEqual(h.stalled_at, f_)
        self.assertTrue(155 <= f_ <= 165, f_)
        self.assertFalse(held.any())
        self.assertTrue(back[:4].all())
        self.assertIsNone(h.update(f_ + 1, floating, NONE, VP, pim, 10.0))           # let go for good

    def test_no_early_let_go_while_closing(self):
        h = GarmentHold(8, stall_frames=60, stall_mm=0.5)
        floating = np.array([False, False, True])
        h.update(1, floating, NONE, VP, PIM, 40.0)
        for f_ in range(2, 200):
            self.assertIsNone(h.update(f_, floating, NONE, VP, PIM, 40.0 - 0.02 * f_))   # 1.2 mm per 60 frames
        self.assertIsNone(h.stalled_at)

    def test_fresh_after_a_resume(self):
        """A resume puts the held particles back to their own mass and clears the hold: holding again is a change."""
        h = GarmentHold(8)
        floating = np.array([False, False, True])
        h.update(1, floating, NONE, VP, PIM, None)
        h.mask[:] = False; h.last = None
        held, on, back = h.update(2, floating, NONE, VP, PIM, None)
        self.assertTrue(on[:4].all())


if __name__ == '__main__':
    unittest.main()
