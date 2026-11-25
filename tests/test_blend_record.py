"""The .blend holds the whole result (blend_record): what the garment keeps, and which folders the clean-up deletes.

Run in Blender, with the folder holding ac9_cloth_sim on the path:
  blender -b --factory-startup --python tests/test_blend_record.py -- <folder holding ac9_cloth_sim>
"""
import json
import os
import sys
import tempfile
import unittest

import bpy

if '--' in sys.argv:
    sys.path.insert(0, sys.argv[sys.argv.index('--') + 1])
from ac9_cloth_sim import blend_record as BR          # noqa: E402
from ac9_cloth_sim import dress as DR                 # noqa: E402


def result(root, name, counts, cfg=None):
    """A result folder of pieces A, B, ... with these vertex counts (each piece a fan of triangles), and its config."""
    import numpy as np
    from ac9_cloth_sim import result_reference as RR
    from ac9_cloth_sim.core import spec as S
    names = [chr(65 + k) for k in range(len(counts))]
    offs = np.cumsum([0] + list(counts[:-1]))
    F, fp, flat = [], [], []
    for k, (o, n) in enumerate(zip(offs, counts)):
        flat += [[100.0 * k + 10.0 * np.cos(t), 10.0 * np.sin(t)] for t in np.linspace(0, 6.0, n)]
        for j in range(1, n - 1):
            F.append([o, o + j, o + j + 1])
            fp.append(k)
    flat = np.array(flat)
    X = np.c_[flat, np.zeros(len(flat))]
    d = os.path.join(root, BR.JOBS_DIR, name)
    spec = {'pieces': [{'name': n, 'edges': [], 'internals': []} for n in names], 'seams': []}
    RR.write_result(d, 'carried', names, offs, np.array(F, np.int32), np.array(fp, np.int32), flat, X,
                    json.dumps({'name': name}))
    if cfg is not None:
        json.dump(cfg, open(os.path.join(d, 's1', 'config.json'), 'w'))
    return d, names, F, fp, X


def show(g, res):
    """g's mesh shows that result (the pieces' faces as Dress writes them)."""
    import numpy as np
    _d, names, F, fp, X = res
    me = bpy.data.meshes.new('shown')
    me.from_pydata([tuple(x) for x in np.asarray(X) * 0.001], [], [tuple(f) for f in F])
    a = me.attributes.new('ac9_piece', 'INT', 'FACE')
    a.data.foreach_set('value', fp)
    g.data = me
    g['ac9_piece_names'] = json.dumps(names)


def job(root, name, cfg=None, info=None):
    d = os.path.join(root, BR.JOBS_DIR, name)
    os.makedirs(os.path.join(d, 's1'), exist_ok=True)
    json.dump(dict({'names': ['A'], 'stages': ['x']}, **(info or {})), open(os.path.join(d, 'job.json'), 'w'))
    open(os.path.join(d, 'scene.idx.npz'), 'wb').write(b'0')
    if cfg is not None:
        json.dump(cfg, open(os.path.join(d, 's1', 'config.json'), 'w'))
        open(os.path.join(d, 's1', 'frames.npy'), 'wb').write(b'0')
    return d


class BlendRecord(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='blend_record_')
        me = bpy.data.meshes.new('m')
        self.g = bpy.data.objects.new(DR.GARMENT_PREFIX + 'test', me)
        bpy.context.scene.collection.objects.link(self.g)
        BR._SAVED_REFS.clear()
        # this test's folder stands for the one beside the .blend
        self._roots = BR._roots
        BR._roots = lambda: {BR._canon(os.path.join(self.root, BR.JOBS_DIR))}
        self._this_file = BR._this_file                  # and this .blend is saved beside it
        BR._this_file = lambda: BR._canon(os.path.join(self.root, 'this.blend'))

    def tearDown(self):
        BR._roots = self._roots
        BR._this_file = self._this_file
        bpy.data.objects.remove(self.g)

    def test_config_from_the_garment_when_the_folder_is_gone(self):
        res = result(self.root, 'run1', [4, 5], cfg={'edge_ke': 0.5, 'stitch_rest_mm': 0.3})
        d = res[0]
        show(self.g, res)
        BR.remember_run(self.g, d)
        self.assertEqual(DR._last_stage_config(d, self.g)['stitch_rest_mm'], 0.3)
        import shutil
        shutil.rmtree(d)
        self.assertEqual(DR._last_stage_config(d), {})
        self.assertEqual(DR._last_stage_config(d, self.g), {'edge_ke': 0.5, 'stitch_rest_mm': 0.3})
        self.assertEqual(json.loads(BR.dressed_spec_text(self.g)), {'name': 'run1'})

    def test_a_garment_object_used_again_does_not_take_over_the_old_record(self):
        old = result(self.root, 'old', [4, 5], cfg={'edge_ke': 0.5})
        show(self.g, old)
        BR.remember_run(self.g, old[0])
        # another result into the garment object of the same name (an import): other vertex counts
        new = result(self.root, 'new', [4, 6])
        show(self.g, new)
        self.g['ac9_job'] = new[0]
        self.assertEqual(BR.last_config(self.g), {})
        self.assertIsNone(BR.dressed_spec_text(self.g))
        import shutil
        shutil.rmtree(new[0])
        self.assertEqual(DR._last_stage_config(new[0], self.g), {})
        # a carry from it does not bring the old config back
        carried = result(self.root, 'carried', [4, 6])
        BR.remember_spec(self.g, '{"name": "carried"}', carried[0], keep_config=BR.record_valid(self.g))
        self.assertEqual(BR.last_config(self.g), {})
        self.assertEqual(json.loads(BR.dressed_spec_text(self.g)), {'name': 'carried'})

    def test_a_carry_keeps_the_config_of_the_run_it_came_from(self):
        run = result(self.root, 'run', [4, 5], cfg={'edge_ke': 0.5})
        show(self.g, run)
        BR.remember_run(self.g, run[0])
        carried = result(self.root, 'carried', [6, 5])         # the pattern edit remeshed a piece
        ok = BR.record_valid(self.g)
        show(self.g, carried)
        BR.remember_spec(self.g, '{"name": "carried"}', carried[0], keep_config=ok)
        self.assertEqual(BR.last_config(self.g), {'edge_ke': 0.5})

    def test_a_file_saved_under_a_new_name_keeps_the_older_files_latest(self):
        # v1 and v2 side by side share AC9_ClothSim_jobs; v2 was saved from v1 and carries v1's history
        v1_latest = job(self.root, 'v1_latest', cfg={})
        v2_old, v2_new = job(self.root, 'v2_old', cfg={}), job(self.root, 'v2_new', cfg={})
        this = BR._this_file
        try:
            BR._this_file = lambda: BR._canon(os.path.join(self.root, 'v1.blend'))
            BR.note(self.g, v1_latest)
            BR._this_file = lambda: BR._canon(os.path.join(self.root, 'v2.blend'))
            BR.note(self.g, v2_old, v2_new)
            self.g['ac9_job'] = v2_new
            n, _ = BR.clean_up([self.g])
        finally:
            BR._this_file = this
        self.assertEqual(n, 1)
        self.assertEqual([os.path.isdir(p) for p in (v1_latest, v2_old, v2_new)], [True, False, True])

    def test_only_the_latest_run_is_kept(self):
        a, b, c = (job(self.root, n, cfg={}) for n in ('r1', 'r2', 'r3'))
        BR.note(self.g, a, b, c)
        self.g['ac9_job'] = c
        n, _ = BR.clean_up([self.g])
        self.assertEqual(n, 2)
        self.assertEqual([os.path.isdir(p) for p in (a, b, c)], [False, False, True])
        self.assertEqual(BR.history(self.g), [os.path.realpath(c)])

    def test_never_deleted(self):
        cur = job(self.root, 'cur', cfg={})
        root_run = job(self.root, 'root', cfg={})
        edit = job(self.root, 'edit', info={'kind': 'pattern edit carried over (no simulation)', 'from': root_run,
                                            'root': root_run})
        other = job(self.root, 'other', cfg={})          # read by another object
        saved = job(self.root, 'saved', cfg={})          # named by the file on disk
        outside = os.path.join(self.root, 'not_jobs', 'x')
        os.makedirs(outside)
        open(os.path.join(outside, 'job.json'), 'w').write('{}')
        ob = bpy.data.objects.new('reader', bpy.data.meshes.new('r'))
        ob['ac9_job'] = other
        BR._SAVED_REFS.add(BR._canon(saved))
        BR.note(self.g, cur, root_run, other, saved, outside, edit)
        self.g['ac9_job'] = edit
        try:
            n, _ = BR.clean_up([self.g])
        finally:
            bpy.data.objects.remove(ob)
        self.assertEqual(n, 1)                         # cur only: edit is current, root_run what it was carried from
        self.assertFalse(os.path.isdir(cur))
        for p in (root_run, edit, other, saved, outside):
            self.assertTrue(os.path.isdir(p), p)

    def test_another_blends_folder_is_not_deleted(self):
        other_root = tempfile.mkdtemp(prefix='blend_record_other_')
        theirs = job(other_root, 'theirs', cfg={})       # beside the .blend this one was saved from
        mine = job(self.root, 'mine', cfg={})
        BR.note(self.g, theirs, mine)
        self.g['ac9_job'] = mine
        n, _ = BR.clean_up([self.g])
        self.assertEqual(n, 0)
        self.assertTrue(os.path.isdir(theirs))

    def test_a_stop_never_touches_the_folder_beside_the_blend(self):
        # A Live's Stop (dress._finish, job_archive.finish/collect) records the result and queues the clean-up. The
        # folder beside the .blend can be on a disk busy renaming a copy there: any check of a path in it from the
        # main thread waited for the rename (6-11 s freezes on a USB hard disk). None is made here.
        import builtins
        import shutil
        from unittest import mock
        from ac9_cloth_sim import job_archive as JA
        a, b = job(self.root, 'r1', cfg={}), job(self.root, 'r2', cfg={})
        slow = os.path.normcase(os.path.abspath(self.root))
        touched, queued = [], []

        import threading
        import time

        def spy(name, real):
            def f(*args, **kw):
                if threading.current_thread() is not threading.main_thread():
                    return real(*args, **kw)          # the disk usage's own thread may read the disk
                for x in args[:1]:
                    if isinstance(x, (str, bytes, os.PathLike)):
                        s = os.path.normcase(os.path.abspath(os.fsdecode(x)))
                        if s.startswith(slow):
                            touched.append((name, s))
                return real(*args, **kw)
            return f
        names = [(os.path, 'realpath'), (os.path, 'isdir'), (os.path, 'exists'), (os.path, 'isfile'),
                 (os, 'stat'), (os, 'lstat'), (os, 'listdir'), (os, 'scandir'), (os, 'walk'), (shutil, 'rmtree'),
                 (builtins, 'open')]
        patches = [mock.patch.object(m, n, spy(n, getattr(m, n))) for m, n in names]
        patches.append(mock.patch.object(JA, 'request_sweep', queued.append))
        for p in patches:
            p.start()
        try:
            BR.note(self.g, a)
            BR.note(self.g, b)
            self.g['ac9_job'] = b
            t0 = time.perf_counter()
            BR._remember_saved()
            BR.auto_clean(self.g)
            BR.clean_on_save()
            BR._USAGE['stamp'] = 0.0
            BR.usage()
            took = time.perf_counter() - t0
            BR.apply_sweep(dict(gone={self.g.name: [BR.history(self.g)[0]]}, missing={}, deleted=1))
            if BR._USAGE['thread'] is not None:
                BR._USAGE['thread'].join(5)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(touched, [])
        self.assertLess(took, 0.1)
        self.assertEqual(len(queued), 2)                  # after the run, after the save
        self.assertEqual(BR.history(self.g), [os.path.normpath(b)])
        self.assertTrue(os.path.isdir(a))                 # the snapshot only queued: nothing deleted here

    def test_the_clean_up_after_a_stop_runs_on_the_archive_thread(self):
        import time
        from ac9_cloth_sim import job_archive as JA
        from ac9_cloth_sim.core import job_workspace as W
        a, b, c = (job(self.root, n, cfg={}) for n in ('r1', 'r2', 'r3'))
        BR.note(self.g, a, b, c)
        self.g['ac9_job'] = c
        writer, JA._WRITER = JA._WRITER, W.ArchiveQueue()
        try:
            BR.auto_clean(self.g)
            for _ in range(200):                          # the thread deletes; collect drops them from the history
                JA.collect()
                if len(BR.history(self.g)) == 1:
                    break
                time.sleep(0.01)
        finally:
            JA._WRITER = writer
        self.assertEqual([os.path.isdir(p) for p in (a, b, c)], [False, False, True])
        self.assertEqual(BR.history(self.g), [os.path.normpath(c)])

    def test_an_undo_before_the_clean_up_ran_keeps_every_folder(self):
        import threading
        import time
        from ac9_cloth_sim import job_archive as JA
        from ac9_cloth_sim.core import job_workspace as W
        a, b = job(self.root, 'r1', cfg={}), job(self.root, 'r2', cfg={})
        BR.note(self.g, a, b)
        self.g['ac9_job'] = b
        gate = threading.Event()
        writer, JA._WRITER = JA._WRITER, W.ArchiveQueue()
        try:
            JA._WRITER.submit_task('hold', gate.wait)    # a copy beside the .blend still being made
            t0 = time.perf_counter()
            BR.auto_clean(self.g)
            queued_s = time.perf_counter() - t0
            JA.invalidate()                               # an undo (or a load) before the clean-up's turn
            gate.set()
            for _ in range(200):
                JA.collect()
                if not JA._WRITER.pending and JA._WRITER.results.empty():
                    break
                time.sleep(0.01)
        finally:
            JA._WRITER = writer
        self.assertLess(queued_s, 0.05)                   # the Stop never waited behind the copy
        self.assertTrue(os.path.isdir(a))
        self.assertEqual(len(BR.history(self.g)), 2)

    def test_undo_to_a_deleted_folder_marks_it_lost(self):
        self.g['ac9_job'] = os.path.join(self.root, BR.JOBS_DIR, 'gone')
        self.g['ac9_piece_names'] = '["A"]'
        DR._drop_missing_cache()
        self.assertTrue(self.g.get(DR.JOB_MISSING_PROP))


if __name__ == '__main__':
    r = unittest.main(argv=['x'], exit=False).result
    sys.exit(0 if r.wasSuccessful() else 1)
