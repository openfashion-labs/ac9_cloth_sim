# SPDX-License-Identifier: GPL-3.0-or-later
import unittest, sys, io, json
from pathlib import Path
import numpy as np
from ac9_cloth_sim.core.cloth_intersection import intersection, direct_sewn


class TriangleIntersection(unittest.TestCase):
    def setUp(self):
        self.a = np.array([[-1., -1, 0], [1, -1, 0], [0, 1, 0]])

    def test_segment_with_both_ends_on_both_boundaries(self):
        b = np.array([[-1., 0, -1], [1, 0, -1], [0, 0, 1]])
        kind, p = intersection(self.a, b)
        self.assertEqual(kind, 'cross')
        self.assertAlmostEqual(np.linalg.norm(p[1] - p[0]), 1.)

    def test_separate_and_coplanar_contact_are_not_crossings(self):
        self.assertIsNone(intersection(self.a, self.a + (0, 0, 1)))
        self.assertIsNone(intersection(self.a, self.a))

    def test_degenerate_face(self):
        self.assertIsNone(intersection(self.a, np.array([[0., 0, 0], [0, 0, 0], [0, 0, 0]])))

    def test_edge_on_other_plane_is_contact_not_transverse(self):
        a = np.array([[-1., 0, 0], [1, 0, 0], [0, 1, 0]])
        b = np.array([[-1., 0, -1], [1, 0, -1], [0, 0, 1]])
        self.assertIsNone(intersection(a, b))

    def test_direct_sewn_face_pair_only(self):
        self.assertTrue(direct_sewn((0, 1, 2), (3, 4, 5), {(1, 4)}))
        self.assertFalse(direct_sewn((0, 1, 2), (3, 4, 5), {(1, 8), (9, 4)}))

    def test_translation_rotation_and_scale(self):
        b = np.array([[-1., 0, -1], [1, 0, -1], [0, 0, 1]])
        for scale in (.001, 1., 1000.):
            a1 = self.a[:, [2, 0, 1]] * scale + (12, 400, -300)
            b1 = b[:, [2, 0, 1]] * scale + (12, 400, -300)
            _kind, p = intersection(a1, b1)
            self.assertAlmostEqual(np.linalg.norm(p[1] - p[0]), scale, 7)


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TriangleIntersection))
    Path(args[1]).write_text(json.dumps(dict(passed=result.wasSuccessful(), tests=result.testsRun, log=log.getvalue()), indent=2), encoding='utf8')
    if not result.wasSuccessful():
        raise RuntimeError(log.getvalue())
