"""The files a result folder must hold, whichever way it was made (a Dress run, the garment of a .blend opened
without its folder, From Mesh): pattern.npz is made from the folder's own mesh when missing, the stitches are
told apart from "no seams", and From Mesh writes its stitches.

Run in Blender (result_reference imports bpy), with the folder holding ac9_cloth_sim on the path:
  blender -b --python tests/test_job_files.py -- <folder holding ac9_cloth_sim>
"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

if '--' in sys.argv:
    sys.path.insert(0, sys.argv[sys.argv.index('--') + 1])
from ac9_cloth_sim import result_reference as RR          # noqa: E402
from ac9_cloth_sim.core import mesh_to_spec as M2S       # noqa: E402
from ac9_cloth_sim.core import mirror_result as MR       # noqa: E402


def two_squares():
    """Two 100 mm squares side by side, each its own triangles: (offs, flat, F, names). Outline vertices first."""
    sq = np.array([[0, 0], [100, 0], [100, 100], [0, 100], [50, 50]], float)
    tri = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    flat = np.vstack([sq, sq + [150, 0]])
    return np.array([0, 5]), flat, np.vstack([tri, tri + 5]), ['A', 'B']


class JobFiles(unittest.TestCase):
    def folder(self, with_names=True):
        d = tempfile.mkdtemp(prefix='job_files_')
        offs, flat, F, names = two_squares()
        np.savez(os.path.join(d, 'scene.idx.npz'), offs=offs, flat=flat, F=F, fp=np.repeat([0, 1], 4),
                 Xinit=np.c_[flat, np.zeros(len(flat))])
        json.dump({'names': names} if with_names else {}, open(os.path.join(d, 'job.json'), 'w'))
        return d

    def test_pattern_from_index(self):
        offs, flat, F, names = two_squares()
        p = RR.pattern_from_index({'offs': offs, 'flat': flat, 'F': F}, names)
        self.assertEqual([str(n) for n in p['names']], names)
        np.testing.assert_array_equal(p['V1'], flat[5:])
        np.testing.assert_array_equal(p['nb'], [4, 4])
        self.assertAlmostEqual(p['h'], (100 + 50 * 2 ** 0.5) / 2, places=6)     # 4 sides and 4 half diagonals

    def test_no_nb_when_the_outline_is_not_first(self):
        offs, flat, F, names = two_squares()
        F = F.copy()
        F[:4] = np.where(F[:4] == 0, 4, np.where(F[:4] == 4, 0, F[:4]))    # piece A: its center vertex first
        flat = flat.copy()
        flat[[0, 4]] = flat[[4, 0]]
        p = RR.pattern_from_index({'offs': offs, 'flat': flat, 'F': F}, names)
        self.assertNotIn('nb', p)

    def test_missing_pattern_is_made_once_from_the_folder(self):
        d = self.folder()
        pat = RR.job_pattern(d)
        self.assertTrue(os.path.isfile(os.path.join(d, 'pattern.npz')))
        self.assertEqual([str(n) for n in pat['names']], ['A', 'B'])
        self.assertIn('nb', pat.files)

    def test_unreadable_folder_is_a_refusal(self):
        d = self.folder(with_names=False)
        with self.assertRaises(ValueError):
            RR.job_pattern(d)
        os.remove(os.path.join(d, 'job.json'))
        with self.assertRaises(ValueError):
            RR.job_pattern(d)

    def test_unknown_stitches_differ_from_none(self):
        self.assertIsNone(RR.stitches({'F': 0}))
        self.assertEqual(len(RR.stitches({'E_st': np.empty((0, 2))})), 0)
        d = self.folder()
        self.assertIsNone(RR.stitches(np.load(os.path.join(d, 'scene.idx.npz'))))

    def test_mirror_report_without_stitches(self):
        self.assertEqual(MR.opened_seams(np.zeros((10, 3)), {'F': 0}, {'A': (0, 5)}, ['A']), [])

    def test_dressed_pattern_from_the_mesh(self):
        """A piece edited after the garment was dressed (on another PC) is laid back on the mesh, same edges."""
        from ac9_cloth_sim.core import transfer as TR
        offs, flat, F, names = two_squares()
        pat = RR.pattern_from_index({'offs': offs, 'flat': flat, 'F': F}, names)
        idx = {'offs': offs, 'flat': flat, 'F': F, 'fp': np.repeat([0, 1], 4)}

        def square(name, x0, s=1.0):
            P = [[x0, 0], [x0 + 100 * s, 0], [x0 + 100 * s, 100 * s], [x0, 100 * s]]
            return {'name': name, 'enabled': True, 'internals': [],
                    'edges': [{'id': f'e{k}', 'pts': [P[k], P[(k + 1) % 4]]} for k in range(4)]}
        spec = {'pieces': [square('A', 0, 1.1), square('B', 150)], 'seams': []}
        dressed, laid, failed = TR.dressed_spec_from_mesh(spec, pat, idx)
        self.assertEqual((laid, failed), (['A'], []))
        a = dressed['pieces'][0]
        self.assertEqual([e['id'] for e in a['edges']], ['e0', 'e1', 'e2', 'e3'])
        np.testing.assert_allclose([e['pts'][0] for e in a['edges']], flat[:4])
        self.assertEqual(dressed['pieces'][1], spec['pieces'][1])           # B not edited: untouched
        self.assertEqual(TR.shape_changed(dressed, pat, idx), [])

    def test_a_piece_with_a_hole_is_not_laid(self):
        from ac9_cloth_sim.core import transfer as TR
        # a square ring: outer 0..3, inner 4..7 -- two outlines, not one ring
        V = np.array([[0, 0], [30, 0], [30, 30], [0, 30], [10, 10], [20, 10], [20, 20], [10, 20]], float)
        F = np.array([[0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
        piece = {'name': 'R', 'edges': [{'id': f'e{k}', 'pts': [V[k].tolist(), V[(k + 1) % 4].tolist()]}
                                        for k in range(4)]}
        self.assertIsNone(TR.piece_from_mesh(piece, V, F))

    def test_a_button_says_any_failure_in_one_line(self):
        from ac9_cloth_sim import dress as D
        self.assertEqual(D.refusal(ValueError("no result")), "no result")
        try:
            {}['E_st']
        except KeyError as exc:
            self.assertEqual(D.refusal(exc), "KeyError: 'E_st'")

    def test_from_mesh_writes_its_stitches(self):
        # two squares sharing the 3D edge x = 100, cut apart in UV: one seam of one edge, two stitches
        V3 = np.array([[0, 0, 0], [100, 0, 0], [200, 0, 0], [0, 0, 100], [100, 0, 100], [200, 0, 100]], float)
        T = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]])
        uv = V3[:, [0, 2]] / 1000.0
        UV = uv[T].copy()
        UV[2:] += [0.5, 0.0]                      # the right square its own island
        spec, job, _ = M2S.convert('T', V3, T, UV)
        self.assertEqual(len(spec['seams']), 1)
        E = job['E_st']
        self.assertEqual(len(E), 2)
        np.testing.assert_allclose(job['X'][E[:, 0]], job['X'][E[:, 1]])
        self.assertEqual(set(job['E_seam'].tolist()), {spec['seams'][0]['id']})


if __name__ == '__main__':
    r = unittest.main(argv=['test_job_files'], exit=False).result
    print('AC9 TEST RESULT', 'OK' if r.wasSuccessful() else 'FAILED', len(r.failures), len(r.errors))
