"""The garment's shape and its result folder stay read and written in their own places (no Blender: a file scan).

Why (2026-11-23): two bugs fixed before came back by another road. A garment's mesh and the result folder it points
at (ac9_job) are written apart, and new code read one from each: the 3D internal lines took the piece ranges from
the mesh and the pattern coordinates from an older folder (lines drawn up to 825 mm off), and a run took a folder
that did not describe the mesh (a collar started 114 mm from where it was shown, the start check silent). The fix
put one rule in garment_view; this test keeps new code from going round it again. The counts are today's: a new
place that writes ac9_job or reads a result index by hand fails here -- go through result_reference /
garment_view, or raise the count with a reason in the same change.

Run: python tests/test_shape_source_guard.py (from the add-on folder; no Blender)."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = ('tests', 'solver', '__pycache__', 'LLM', 'docs')

# who points a garment at a result folder: g['ac9_job'] = ...
JOB_WRITE = re.compile(r"\[['\"]ac9_job['\"]\]\s*=[^=]")
JOB_WRITE_ALLOWED = {'dress.py': 5, 'draft_tool.py': 1, 'export_retopo.py': 1, 'job_archive.py': 4,
                     'mesh_import.py': 1, 'result_reference.py': 1}
# who opens a result's index file by hand (np.load of RR.INDEX) instead of result_reference.open_result
INDEX_READ = re.compile(r"RR\.INDEX")
INDEX_READ_ALLOWED = {'build_ahead.py': 1, 'button_view.py': 2, 'dress.py': 1, 'live_resume.py': 3,
                      'overlay.py': 2, 'placement.py': 1, 'start_check.py': 2, 'zipper_view.py': 2}
# the seam index the overlay verified (ranges and pattern coordinates read together): the overlay's own
JOBSEG = re.compile(r"\b_jobseg\b")
JOBSEG_ALLOWED = {'overlay.py': 10 ** 6}


def _sources():
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith('.py'):
                p = os.path.join(root, f)
                yield os.path.relpath(p, HERE).replace(os.sep, '/'), open(p, encoding='utf-8').read()


def _over(pattern, allowed):
    out = []
    for rel, text in _sources():
        n = len(pattern.findall(text))
        if n > allowed.get(rel, 0):
            out.append(f"{rel}: {n} (allowed {allowed.get(rel, 0)})")
    return out


class ShapeSourceGuard(unittest.TestCase):
    def test_ac9_job_written_in_known_places_only(self):
        self.assertEqual(_over(JOB_WRITE, JOB_WRITE_ALLOWED), [],
                         "a new place points the garment at a result folder: the mesh and the folder must change "
                         "together (garment_view); go through an existing writer or raise the count with a reason")

    def test_result_index_read_by_hand_in_known_places_only(self):
        self.assertEqual(_over(INDEX_READ, INDEX_READ_ALLOWED), [],
                         "a new place reads a result's index by hand: read it through result_reference, and ask "
                         "garment_view.fits(g, job) first when it is the garment's folder")

    def test_overlay_seam_index_not_read_elsewhere(self):
        self.assertEqual(_over(JOBSEG, JOBSEG_ALLOWED), [])

    def test_overlay_reads_ranges_and_flat_from_one_index(self):
        text = open(os.path.join(HERE, 'overlay.py'), encoding='utf-8').read()
        # the 3D notches and internal lines: never the job's flat next to the verified index's ranges
        for fn in ('def notches3d', 'def _lines_to_3d'):
            body = text.split(fn, 1)[1].split('\ndef ', 1)[0]
            self.assertNotIn('job_of(', body, fn)
            self.assertNotIn('RR.INDEX', body, fn)
            self.assertIn('_result_rng(', body, fn)

    def test_runs_ask_whether_the_folder_describes_the_mesh(self):
        text = open(os.path.join(HERE, 'dress.py'), encoding='utf-8').read()
        body = text.split('def job_or_from_mesh', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('ensure_fits(', body)
        body = text.split('def _piece_ranges', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('.fits(', body)

    def test_allow_lists_name_files_that_exist(self):
        names = {rel for rel, _t in _sources()}
        for allowed in (JOB_WRITE_ALLOWED, INDEX_READ_ALLOWED, JOBSEG_ALLOWED):
            self.assertEqual(sorted(set(allowed) - names), [])


if __name__ == '__main__':
    unittest.main()
