# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary fitting scope keeps collision meshes and simulation inputs intact."""
import unittest
import importlib.util
from pathlib import Path
import numpy as np
_spec = importlib.util.spec_from_file_location('place_target_scope', Path(__file__).resolve().parents[1] / 'core/place_targets.py')
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


class TestScope(unittest.TestCase):
    def setUp(self):
        self.st = np.array([[0, 1, 1, 2], [2, 3, 0, 4], [0, 5, 0, 6], [1, 7, 2, 8]])
        self.pat = dict(names=['collar', 'front', 'back'], stitch=self.st, stitch_off=np.array([9]),
                        stitch_seam=np.array(['front_join','back_join','closure','shoulder']))
        self.tris = [np.array([[0, 1, 2]])] * 3
        self.xyz = [np.zeros((9, 3))] * 3
        self.got = self.pat, self.tris, self.xyz

    def test_one_target_keeps_self_and_other_stitches(self):
        pat, tris, xyz = P.filter_stitches(self.got, 'collar', {'front'})
        np.testing.assert_array_equal(pat['stitch'], self.st[[0, 2, 3]])
        np.testing.assert_array_equal(self.pat['stitch'], self.st)
        self.assertIs(tris, self.tris)
        self.assertIs(xyz, self.xyz)
        self.assertIs(pat['stitch_off'], self.pat['stitch_off'])
        self.assertEqual(pat['stitch_seam'].tolist(), ['front_join','closure','shoulder'])

    def test_reverse_row(self):
        pat, _, _ = P.filter_stitches(self.got, 'collar', {'back'})
        np.testing.assert_array_equal(pat['stitch'], self.st[[1, 2, 3]])

    def test_all_matches_existing(self):
        pat, _, _ = P.filter_stitches(self.got, 'collar', {'front', 'back'})
        np.testing.assert_array_equal(pat['stitch'], self.st)

    def test_empty_is_not_all(self):
        pat, _, _ = P.filter_stitches(self.got, 'collar', set())
        np.testing.assert_array_equal(pat['stitch'], self.st[[2, 3]])

    def test_partners_exclude_self_and_pair(self):
        self.assertEqual(P.partner_names(self.got, 'collar'), ['back', 'front'])
        self.assertEqual(P.partner_names(self.got, 'collar', ['back']), ['front'])

    def test_eligibility_view_does_not_change_spec(self):
        sp = dict(pieces=[{'name': 'collar', 'enabled': True}], seams=[
            dict(id=str(i), a={'piece': a}, b={'piece': b}, enabled=True)
            for i, (a, b) in enumerate([('collar', 'front'), ('back', 'collar'),
                                        ('collar', 'collar'), ('back', 'front')])])
        view = P.filter_spec(sp, 'collar', {'front'})
        self.assertEqual([s['id'] for s in view['seams']], ['0', '2', '3'])
        self.assertEqual(len(sp['seams']), 4)
        self.assertTrue(all(s['enabled'] for s in sp['seams']))


if __name__ == '__main__':
    unittest.main()
