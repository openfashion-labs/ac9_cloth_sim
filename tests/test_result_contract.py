"""The result folder's contract (result_reference.py): whichever way a folder was written -- the garment of a .blend
opened without its folder (dress.job_from_mesh), From Mesh (mesh_import.write_job), a pattern edit carried to the 3D
(draft_tool.carry_to_3d, through result_reference.write_result) -- it holds REQUIRED, and every reader that a button
reaches returns a value or refuses with a ValueError (a one-line refusal) on it as written, with its frames dropped,
and with each of its files taken away. An OSError / KeyError / TypeError out of a reader is a traceback for the user
(a .blend from another PC, 2026-11-14) and fails here.

Also: the result files' names are written only in result_reference.py, outside the run's own writer and stages
(dress.py), whose count may only go down.

Run in Blender (bpy), with the folder holding ac9_cloth_sim on the path:
  blender -b --python tests/test_result_contract.py -- <folder holding ac9_cloth_sim>
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

import numpy as np

if '--' in sys.argv:
    sys.path.insert(0, sys.argv[sys.argv.index('--') + 1])
import bpy                                                         # noqa: E402
from ac9_cloth_sim import dress as DR                              # noqa: E402
from ac9_cloth_sim import draft_tool as DT                         # noqa: E402
from ac9_cloth_sim import export_retopo as ER                      # noqa: E402
from ac9_cloth_sim import mesh_import as MI                        # noqa: E402
from ac9_cloth_sim import result_reference as RR                   # noqa: E402
from ac9_cloth_sim.core import mesh_to_spec as M2S                 # noqa: E402
from ac9_cloth_sim.core import mirror_result as MR                 # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES_RE = re.compile(r"'(pattern\.npz|scene\.idx\.npz|avatar\.npz|job\.json|ac9_spec\.json|final\.npy|frames\.npy)'")
# the run's own writer, its stages and frame handling (dress.py _prepare, the operator), the prepared candidate and
# the cache cleanup: they write and read the folder they made in the same run. May go down, not up.
ALLOWED = {'dress.py': 29, 'prepare_build.py': 1, os.path.join('core', 'prepared_candidate.py'): 2}


def two_squares():
    """Two 100 mm squares, each its own piece (outline vertices first), sewn along one side: one seam, 2 stitches."""
    sq = np.array([[0, 0], [100, 0], [100, 100], [0, 100], [50, 50]], float)
    tri = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    flat = np.vstack([sq, sq + [150, 0]])
    X = np.c_[flat[:, 0], np.zeros(len(flat)), flat[:, 1]]
    X[5:, 0] -= 50.0                                   # the right square's left side on the left square's right side
    return ['A', 'B'], np.array([0, 5]), np.vstack([tri, tri + 5]), np.repeat([0, 1], 4), flat, X


class Contract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='result_contract_')
        self.made = []

    def tearDown(self):
        for ob in self.made:
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except ReferenceError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the folders, one per writer
    def garment(self, names, offs, F, fp, flat, X, job=None):
        ob = DR.garment_object(bpy.context, 'AC9_Garment_Contract', flat, F, fp, X, names)
        self.made.append(ob)
        if job:
            ob['ac9_job'] = job
        return ob

    def written(self, kind):
        names, offs, F, fp, flat, X = two_squares()
        d = os.path.join(self.tmp, kind.replace(' ', '_'))
        if kind == 'from the .blend':
            g = self.garment(names, offs, F, fp, flat, X)
            g[DR.JOB_MISSING_PROP] = 'elsewhere'
            j = DR.job_from_mesh(bpy.context, g, 'elsewhere')
            self.assertIsNotNone(j)
            shutil.move(j, d)
            g['ac9_job'] = d
        elif kind == 'from mesh':
            V3 = np.array([[0, 0, 0], [100, 0, 0], [200, 0, 0], [0, 0, 100], [100, 0, 100], [200, 0, 100]], float)
            T = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]])
            UV = (V3[:, [0, 2]] / 1000.0)[T].copy()
            UV[2:] += [0.5, 0.0]
            spec, job, _ = M2S.convert('T', V3, T, UV)
            j, g = MI.write_job(bpy.context, spec, job)
            self.made.append(g)
            shutil.move(j, d)
            g['ac9_job'] = d
        else:
            RR.write_result(d, kind, names, offs, F, fp, flat, X, '{}', stitches=[[1, 5], [2, 8]],
                            seams=['s1', 's1'], info={'from': ''})
            g = self.garment(names, offs, F, fp, flat, X, d)
        return d, g

    def test_writers_meet_the_contract(self):
        for kind in ('from the .blend', 'from mesh', 'carried'):
            with self.subTest(kind=kind):
                d, _ = self.written(kind)
                self.assertEqual(RR.check(d), [])
                res = RR.open_result(d)
                self.assertEqual(len(res.names), len(res.index['offs']))

    def test_from_mesh_and_carried_know_their_stitches(self):
        for kind in ('from mesh', 'carried'):
            with self.subTest(kind=kind):
                d, _ = self.written(kind)
                E = RR.open_result(d).stitches()
                self.assertIsNotNone(E)
                self.assertEqual(len(E), 2)

    # ---- every reader on every state of every folder
    def states(self, d):
        """(label, folder) for the folder as written, its frames dropped, and each file taken away."""
        out = [('as written', d)]
        k = d + '_dropped'
        shutil.copytree(d, k)
        np.save(os.path.join(k, RR.FINAL), RR.last_frame(k).astype(np.float32))
        shutil.rmtree(os.path.join(k, 's1'))
        out.append(('frames dropped', k))
        for f in (RR.JOB, RR.INDEX, RR.PATTERN, RR.SPEC, os.path.join('s1', RR.FRAMES)):
            if not os.path.isfile(os.path.join(d, f)):          # (no pattern in the scene: no ac9_spec.json)
                continue
            c = d + '_no_' + os.path.basename(f).replace('.', '_')
            shutil.copytree(d, c)
            os.remove(os.path.join(c, f))
            out.append(('without ' + f, c))
        return out

    def readers(self, job, g):
        names, offs, F, fp, flat, X = two_squares()
        pat = {'names': np.array(names), 'V0': flat[:5], 'V1': flat[5:]}
        res = RR.open_result(job)
        return {
            'open_result.info': lambda: res.info,
            'open_result.names': lambda: res.names,
            'open_result.piece_ranges': lambda: res.piece_ranges(),
            'open_result.pattern': lambda: res.pattern(),
            'open_result.stitches': lambda: res.stitches(),
            'open_result.avatar': lambda: res.avatar(),
            'open_result.last_shape': lambda: res.last_shape(required=True),
            'open_result.dressed_spec_text': lambda: res.dressed_spec_text(),
            'draft_tool._last_result': lambda: DT._last_result(job),
            'draft_tool._simulated_root': lambda: DT._simulated_root(job),
            'dress._from_last_result': lambda: DR._from_last_result(job, pat, [flat[:5] * 0, flat[5:] * 0]),
            'dress._last_stage_config': lambda: DR._last_stage_config(job),
            'dress._job_info': lambda: DR._job_info(job),
            'dress._mesh_back': lambda: DR._mesh_back(bpy.context, g, job),
            'dress._piece_ranges': lambda: DR._piece_ranges(g),
            'dress.garment_pieces': lambda: DR.garment_pieces(g),
            'export_retopo.build_guide': lambda: ER.build_guide(bpy.context, g),
            'mirror_result.opened_seams': lambda: MR.opened_seams(np.zeros((10, 3)), res.index,
                                                                  {'A': (0, 5)}, ['A']),
        }

    def test_readers_refuse_in_one_line_or_answer(self):
        bad = []
        for kind in ('from the .blend', 'from mesh', 'carried'):
            d, g = self.written(kind)
            for label, job in self.states(d):
                g['ac9_job'] = job
                DR._gp.clear()
                for name, fn in self.readers(job, g).items():
                    try:
                        fn()
                    except ValueError:
                        pass                       # a refusal, said in one line by the button that called it
                    except Exception as exc:       # noqa: BLE001 -- what this test is for
                        bad.append(f"{kind} / {label} / {name}: {type(exc).__name__}: {exc}")
                for ob in [o for o in bpy.data.objects if o.name.startswith(ER.GUIDE_PREFIX)]:
                    bpy.data.objects.remove(ob, do_unlink=True)
        self.assertEqual(bad, [])

    # ---- the names live in one place
    def test_result_file_names_only_in_the_contract(self):
        over = []
        for root, dirs, files in os.walk(HERE):
            dirs[:] = [x for x in dirs if x not in ('tests', 'solver', '__pycache__', 'LLM', 'docs')]
            for f in files:
                if not f.endswith('.py') or f == 'result_reference.py':
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, HERE)
                n = len(NAMES_RE.findall(open(p, encoding='utf-8').read()))
                if n > ALLOWED.get(rel, 0):
                    over.append(f"{rel}: {n} (allowed {ALLOWED.get(rel, 0)})")
        self.assertEqual(over, [], "read and write result files through result_reference (open_result, "
                                   "write_result) and its names (RR.INDEX ...)")


if __name__ == '__main__':
    r = unittest.main(argv=['test_result_contract'], exit=False).result
    print('AC9 TEST RESULT', 'OK' if r.wasSuccessful() else 'FAILED', len(r.failures), len(r.errors))
