# SPDX-License-Identifier: GPL-3.0-or-later
"""Judging and deleting a garment's old result folders off Blender's main thread (core/old_runs), and the archive
thread's tasks running in order with its copies (core/job_workspace.ArchiveQueue.submit_task).

Run with Blender's Python, the folder holding ac9_cloth_sim on PYTHONPATH:
  python -m unittest tests.test_old_runs
"""
import json
import os
import tempfile
import threading
import time
import unittest

from ac9_cloth_sim.core import job_workspace as W
from ac9_cloth_sim.core import old_runs as OR


def job(root, name, info=None):
    d = os.path.join(root, OR.JOBS_DIR, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, OR.JOB), 'w', encoding='utf-8') as f:
        json.dump(info or {}, f)
    return d


class OldRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.this = os.path.join(self.root, 'this.blend')

    def snap(self, entries, current, **kw):
        e = [{'path': p, 'file': kw.pop('file', self.this)} for p in entries]
        return dict(dict(this_file=self.this, roots=[os.path.join(self.root, OR.JOBS_DIR)], temp_roots=[], saved=[],
                         readers=[], busy=[], garments=[{'name': 'G', 'entries': e, 'current': current}]), **kw)

    def test_the_latest_and_what_it_was_carried_from_are_kept(self):
        a, root_run = job(self.root, 'a'), job(self.root, 'root')
        edit = job(self.root, 'edit', {'kind': 'pattern edit carried over', 'from': root_run, 'root': root_run})
        out = OR.sweep(self.snap([a, root_run, edit], [edit]))
        self.assertEqual(out['gone'], {'G': [a]})
        self.assertEqual([os.path.isdir(p) for p in (a, root_run, edit)], [False, True, True])

    def test_read_saved_busy_and_foreign_folders_are_kept(self):
        cur, r, s, b = (job(self.root, n) for n in ('cur', 'read', 'saved', 'busy'))
        other = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(other) and __import__('shutil').rmtree(other))
        theirs = job(other, 'theirs')
        out = OR.sweep(self.snap([r, s, b, theirs, cur], [cur], readers=[r], saved=[s], busy=[b]))
        self.assertEqual(out['deleted'], 0)
        self.assertTrue(all(os.path.isdir(p) for p in (cur, r, s, b, theirs)))

    def test_written_while_another_blend_was_open_is_kept(self):
        a, cur = job(self.root, 'a'), job(self.root, 'cur')
        out = OR.sweep(self.snap([a, cur], [cur], file=os.path.join(self.root, 'other.blend')))
        self.assertEqual(out['deleted'], 0)

    def test_still_free_vetoes_a_deletion_and_missing_folders_are_reported(self):
        a, b, cur = job(self.root, 'a'), job(self.root, 'b'), job(self.root, 'cur')
        gone = os.path.join(self.root, OR.JOBS_DIR, 'gone_already')
        out = OR.sweep(self.snap([a, b, gone, cur], [cur]), still_free=lambda p: p != b)
        self.assertEqual(out['gone'], {'G': [a]})
        self.assertEqual(out['missing'], {'G': [gone]})
        self.assertTrue(os.path.isdir(b))


class ArchiveTasks(unittest.TestCase):
    def test_a_task_runs_after_the_copy_queued_before_it_and_reports(self):
        q = W.ArchiveQueue()
        gate, order = threading.Event(), []
        q.submit_task('copy', lambda: (gate.wait(), order.append('copy'))[1])
        t0 = time.perf_counter()
        q.submit_task('old_runs', lambda: order.append('clean') or 7)
        self.assertLess(time.perf_counter() - t0, 0.05)   # queuing never waits for the one running
        q.submit_task('broken', lambda: 1 / 0)
        gate.set()
        rows = []
        for _ in range(300):
            rows += q.collect()
            if len(rows) == 3:
                break
            time.sleep(0.01)
        self.assertEqual(order, ['copy', 'clean'])
        self.assertEqual([r['task'] for r in rows], ['copy', 'old_runs', 'broken'])
        self.assertEqual(rows[1]['value'], 7)
        self.assertTrue(rows[2]['error'])
        self.assertEqual(q.pending, set())


if __name__ == '__main__':
    unittest.main()
