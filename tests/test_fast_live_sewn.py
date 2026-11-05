# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import numpy as np

from solver.fast_live.sewn import sewn


class SewnTest(unittest.TestCase):
    def setUp(self):
        # 2 pattern stitches (closed to 1 mm and 2 mm), then an anchor spring and a seam_paths target spring 0.5 m away
        self.q = np.array([[0, 0, 0], [0.001, 0, 0], [0, 1, 0], [0, 1.002, 0], [0, 0, 0.5], [0, 0, 1.0]], float)
        self.indices = np.array([0, 1, 2, 3, 4, 5, 0, 5], np.int32)
        self.ke = np.array([1000., 1000., 50., 1000.], np.float32)

    def test_springs_after_the_pattern_stitches_do_not_hold_it_on_native(self):
        self.assertTrue(sewn(self.q, self.indices, self.ke, 2, 0.003))
        self.assertFalse(sewn(self.q, self.indices, self.ke, 4, 0.003))

    def test_one_open_pattern_stitch_keeps_it_on_native(self):
        q = self.q.copy(); q[3, 1] += 0.01
        self.assertFalse(sewn(q, self.indices, self.ke, 2, 0.003))

    def test_no_pattern_stitch_is_sewn(self):
        self.assertTrue(sewn(self.q, self.indices, self.ke, 0, 0.003))
        self.assertTrue(sewn(self.q, self.indices, np.zeros(4, np.float32), 2, 0.003))


if __name__ == '__main__':
    unittest.main()
