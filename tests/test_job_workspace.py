# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional result export: failure must preserve the working result."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ac9_cloth_sim.core import job_workspace as W


class JobWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'local' / 'job_unique'
        self.target = self.root / 'saved' / 'AC9_ClothSim_jobs' / self.source.name
        self.source.mkdir(parents=True)
        (self.source/'job.json').write_text(json.dumps(dict(stages=[], dir=str(self.source),
            pattern=str(self.source/'pattern.npz'))), encoding='utf8')
        for file in ('scene.idx.npz', 'pattern.npz', 'final.npy'):
            (self.source/file).write_bytes(b'authentic payload\0')

    def test_publish_and_remap_paths_without_mutating_source(self):
        old = (self.source/'job.json').read_bytes()
        self.assertEqual(W.archive_finished(self.source, self.target), str(self.target))
        self.assertEqual(W.archived_path(self.source), str(self.target))
        self.assertEqual((self.source/'job.json').read_bytes(), old)
        saved = json.loads((self.target/'job.json').read_text())
        self.assertEqual(saved['dir'], str(self.target))
        self.assertEqual(saved['pattern'], str(self.target/'pattern.npz'))
        self.assertEqual((self.target/'final.npy').read_bytes(), (self.source/'final.npy').read_bytes())

    def test_existing_destination_is_not_overwritten(self):
        self.target.mkdir(parents=True)
        (self.target/'valuable').write_text('keep')
        with self.assertRaises(FileExistsError):
            W.archive_finished(self.source, self.target)
        self.assertEqual((self.target/'valuable').read_text(), 'keep')
        self.assertIsNone(W.archived_path(self.source))

    def test_source_changed_during_copy_is_not_committed(self):
        original = W.shutil.copyfile
        def change(src, dst):
            result = original(src, dst)
            if Path(src).name == 'job.json':
                (self.source/'final.npy').write_bytes(b'changed')
            return result
        with patch.object(W.shutil, 'copyfile', side_effect=change):
            with self.assertRaises(ValueError):
                W.archive_finished(self.source, self.target)
        self.assertFalse((self.target/W.RECEIPT).exists())      # left visibly incomplete, never a saved job
        self.assertIsNone(W.archived_path(self.source))
        self.assertEqual((self.source/'final.npy').read_bytes(), b'changed')

    def test_write_failure_preserves_reference_and_working_result(self):
        with patch.object(W.shutil, 'copyfile', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                W.archive_finished(self.source, self.target)
        self.assertFalse((self.target/W.RECEIPT).exists())
        self.assertTrue((self.source/'final.npy').is_file())
        self.assertIsNone(W.archived_path(self.source))

    def test_missing_required_file_refuses_export(self):
        (self.source/'pattern.npz').unlink()
        with self.assertRaises(ValueError):
            W.archive_finished(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_job_name_and_nested_target_are_rejected(self):
        for target in (self.source, self.source/'nested', self.target.with_name('other')):
            with self.assertRaises(ValueError):
                W.archive_finished(self.source, target)

    def test_success_is_idempotent_but_foreign_receipt_is_rejected(self):
        W.archive_finished(self.source, self.target)
        self.assertEqual(W.archive_finished(self.source, self.target), str(self.target))
        data = json.loads((self.source/W.LINK).read_text())
        data['token'] = 'foreign'
        (self.source/W.LINK).write_text(json.dumps(data))
        self.assertIsNone(W.archived_path(self.source))

    def test_queue_records_result_without_calling_blender(self):
        writer = W.ArchiveQueue()
        self.assertTrue(writer.submit(self.source, self.target))
        writer.requests.join()
        rows = writer.collect()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['error'])
        self.assertEqual(rows[0]['target'], str(self.target))

    def test_only_owned_path_fields_are_remapped(self):
        path = str(self.source/'pattern.npz')
        old_job = str(self.source.parent/'older'/'frames.npy')
        data = dict(pattern=path, note=path, asset=dict(path=path), init_from=old_job)
        (self.source/'config.json').write_text(json.dumps(data))
        W.archive_finished(self.source, self.target)
        saved = json.loads((self.target/'config.json').read_text())
        self.assertEqual(saved['pattern'], str(self.target/'pattern.npz'))
        self.assertEqual(saved['note'], path)
        self.assertEqual(saved['asset']['path'], path)
        self.assertEqual(saved['init_from'], old_job)

    def test_retry_refuses_modified_saved_payload(self):
        W.archive_finished(self.source, self.target)
        (self.target/'final.npy').write_bytes(b'foreign')
        with self.assertRaises((ValueError,FileExistsError)):
            W.archive_finished(self.source, self.target)

    def test_retry_repairs_link_failure_after_publish_in_both_copy_stages(self):
        W.seal_finished(self.source,self.lease())
        recovery=self.root/'recovery'/'AC9_ClothSim_jobs'/self.source.name
        for source,target in ((self.source,recovery),(recovery,self.target)):
            write=W._write
            def fail_link(path,value):
                if path==source/W.LINK:raise OSError('injected after publish')
                return write(path,value)
            with patch.object(W,'_write',side_effect=fail_link),self.assertRaises(OSError):
                W.archive_finished(source,target)
            self.assertTrue(W.saved_generation(target,self.lease()['token']))
            self.assertFalse((source/W.LINK).exists())
            self.assertEqual(W.archive_finished(source,target),str(target))
            self.assertEqual(W.archived_path(source),str(target))

    def test_written_in_place_with_the_required_files_last(self):
        """No folder is renamed into place (on a slow disk that stopped every access to it for seconds); the saved
        folder is its final name from the start, a reader requires job.json and RECEIPT, and those arrive last."""
        seen = []
        original = W.shutil.copyfile
        def watch(src, dst):
            seen.append((Path(src).name, (self.target/'job.json').exists(), (self.target/W.RECEIPT).exists(),
                         (self.target/W.PARTIAL).exists()))
            return original(src, dst)
        renamed = []
        with patch.object(W.shutil, 'copyfile', side_effect=watch),                 patch.object(W.Path, 'rename', side_effect=lambda *a: renamed.append(a)):
            W.archive_finished(self.source, self.target)
        self.assertEqual(renamed, [])
        self.assertEqual([name for name, *_ in seen][-3:], ['scene.idx.npz', 'pattern.npz', 'job.json'])
        self.assertTrue(all(not job and not receipt and partial for _n, job, receipt, partial in seen))
        self.assertFalse((self.target/W.PARTIAL).exists())
        self.assertEqual([p.name for p in self.target.iterdir() if p.name.endswith('.tmp')], [])
        self.assertEqual(W.archived_path(self.source), str(self.target))

    def test_an_interrupted_copy_is_finished_by_the_retry(self):
        W.seal_finished(self.source, self.lease())
        original, calls = W.shutil.copyfile, []
        def fail_second(src, dst):
            calls.append(src)
            if len(calls) == 2:
                original(src, dst)                    # half there under its temporary name, then the disk fails
                raise OSError('disk unavailable')
            return original(src, dst)
        with patch.object(W.shutil, 'copyfile', side_effect=fail_second), self.assertRaises(OSError):
            W.archive_finished(self.source, self.target)
        self.assertTrue((self.target/W.PARTIAL).is_file())
        self.assertIsNone(W.archived_path(self.source))
        self.assertFalse(W.saved_generation(self.target, self.lease()['token']))
        self.assertEqual(W.archive_finished(self.source, self.target), str(self.target))
        self.assertTrue(W.saved_generation(self.target, self.lease()['token']))
        self.assertEqual(W.archived_path(self.source), str(self.target))
        self.assertEqual([p.name for p in self.target.rglob('*') if p.name.endswith('.tmp')
                          or p.name == W.PARTIAL], [])

    def test_a_partial_folder_of_another_export_is_not_touched(self):
        self.target.mkdir(parents=True)
        (self.target/W.PARTIAL).write_text(json.dumps(dict(version=1, source=str(self.root/'other'),
            target=str(self.target), generation=None, token='a'*32)))
        (self.target/'final.npy').write_bytes(b'theirs')
        with self.assertRaises(FileExistsError):
            W.archive_finished(self.source, self.target)
        self.assertEqual((self.target/'final.npy').read_bytes(), b'theirs')

    def test_a_cancel_deletes_only_its_own_folder(self):
        self.target.mkdir(parents=True)
        (self.target/W.PARTIAL).write_text(json.dumps(dict(version=1, source=str(self.source),
            target=str(self.target), generation=None, token='b'*32)))
        with self.assertRaises(W.ExportCancelled):
            W.archive_finished(self.source, self.target, cancelled=lambda: True)
        self.assertFalse(self.target.exists())        # this export's own (its PARTIAL names this source)
        other = self.root/'saved2'/'AC9_ClothSim_jobs'/self.source.name
        other.mkdir(parents=True)
        (other/'keep').write_text('x')
        with self.assertRaises(FileExistsError):
            W.archive_finished(self.source, other, cancelled=lambda: True)
        self.assertTrue((other/'keep').is_file())

    def test_no_folder_is_renamed_or_moved_by_the_add_on(self):
        """Renaming a folder of files on a slow disk held every other access to that disk for 1-12 s (measured on a
        USB hard disk); writing and deleting single files did not. Results are written in place and deleted file by
        file (job_workspace.PARTIAL): no rename, move or tree copy of a folder in the add-on's code."""
        import ast
        root = Path(W.__file__).resolve().parent.parent
        found = []
        for path in root.rglob('*.py'):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(('tests/', 'solver/')):
                continue
            tree = ast.parse(path.read_text(encoding='utf8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                    owner = node.func.value.id if isinstance(node.func.value, ast.Name) else ''
                    # any .rename(...) (os.rename, Path.rename); files arrive by os.replace of a single file
                    if (name in ('move', 'copytree') and owner == 'shutil') or name == 'rename':
                        found.append((rel, node.lineno, owner + '.' + name))
        self.assertEqual(found, [])

    def test_path_wait_resolves_like_pathlib(self):
        from ac9_cloth_sim.core import path_wait as PW
        (self.root/'a'/'b').mkdir(parents=True)
        for case in (self.root/'a', self.root/'a'/'b', self.root/'a'/'missing'/'deeper.json',
                     self.root/'a'/'b'/'..'/'b', str(self.root/'a').upper(), self.root/'nope'):
            self.assertEqual(PW.resolve(case), Path(case).resolve(), case)
        self.assertEqual(sorted(PW.names(self.root/'a')), ['b'])

    def test_background_file_code_holds_no_interpreter_lock_while_waiting(self):
        """os.path.realpath / Path.resolve and os.listdir kept Blender's UI waiting as long as a slow disk did when a
        background thread called them (up to 15 s on a USB hard disk); the archive's thread uses path_wait."""
        import ast
        root = Path(W.__file__).resolve().parent
        for name in ('job_workspace.py', 'old_runs.py'):
            tree = ast.parse((root/name).read_text(encoding='utf8'))
            calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and n.func.attr in ('resolve', 'realpath', 'listdir')
                     and not (isinstance(n.func.value, ast.Name) and n.func.value.id in ('PW',))
                     and not (isinstance(n.func.value, ast.Attribute) and n.func.value.attr == 'PW')]
            allowed = 1 if name == 'job_workspace.py' else 0     # work_root, on the main thread, not beside the .blend
            self.assertEqual(len(calls), allowed, (name, calls))

    def test_blank_preference_uses_blender_temp(self):
        self.assertEqual(W.work_root('', str(self.root)), self.root/'AC9_ClothSim_work')
        self.assertEqual(W.work_root(str(self.root/'nvme'), str(self.root)), self.root/'nvme'/'AC9_ClothSim_work')

    def accepted_recovery(self):
        generation=self.lease()['token']
        recovery=self.root/'recovery'/'AC9_ClothSim_jobs'/self.source.name
        W._write(self.source/W.PLAN,dict(version=1,generation=generation,
                 recovery=str(recovery),target=str(self.target)))
        W.seal_finished(self.source,self.lease())
        W.archive_finished(self.source,recovery)
        self.source=recovery
        return generation

    def test_accepted_enqueue_returns_while_worker_filesystem_is_blocked(self):
        import threading
        generation=self.accepted_recovery();entered=threading.Event();release=threading.Event()
        original=W.PW.resolve;main=threading.get_ident();calls=[]
        def delayed(path):
            calls.append(threading.get_ident())
            if threading.get_ident()==main:raise AssertionError('GUI must not resolve an export path')
            entered.set();release.wait(5)
            return original(path)
        writer=W.ArchiveQueue()
        try:
            with patch.object(W.PW,'resolve',delayed):
                self.assertTrue(writer.submit_accepted(str(self.source),str(self.target),generation))
                self.assertTrue(entered.wait(2));self.assertNotIn(main,calls)
                self.assertFalse(writer.submit_accepted(str(self.source),str(self.target),generation))
                with self.assertRaises(ValueError):
                    writer.submit_accepted(str(self.source),str(self.target.parent/'other'),generation)
                self.assertTrue((self.source/W.SEAL).is_file())
                release.set();writer.requests.join()
        finally:release.set()
        row,=writer.collect();self.assertIsNone(row['error'])
        self.assertEqual(row['source'],str(self.source));self.assertTrue(W.saved_generation(self.target,generation))

    def test_accepted_wrong_plan_destination_is_not_published(self):
        generation=self.accepted_recovery();writer=W.ArchiveQueue()
        wrong=self.target.parent.parent/'other'/'AC9_ClothSim_jobs'/self.source.name
        writer.submit_accepted(str(self.source),str(wrong),generation);writer.requests.join()
        row,=writer.collect();self.assertIn('sealed recovery plan',row['error'])
        self.assertFalse(wrong.exists());self.assertTrue((self.source/W.SEAL).exists())

    def test_accepted_export_failure_can_retry_from_durable_source(self):
        generation=self.accepted_recovery();writer=W.ArchiveQueue()
        with patch.object(W,'archive_finished',side_effect=OSError('O disconnected')):
            writer.submit_accepted(str(self.source),str(self.target),generation);writer.requests.join()
        self.assertIn('O disconnected',writer.collect()[0]['error'])
        self.assertTrue(W.saved_generation(self.source,generation))
        writer.submit_accepted(str(self.source),str(self.target),generation);writer.requests.join()
        self.assertIsNone(writer.collect()[0]['error']);self.assertTrue(W.saved_generation(self.target,generation))

    def test_accepted_logical_destination_is_resolved_by_publication_worker(self):
        generation=self.accepted_recovery();writer=W.ArchiveQueue()
        logical=self.target;physical=self.root/'retargeted'/'AC9_ClothSim_jobs'/self.source.name
        original=W.PW.resolve
        def retarget(path):
            return physical if Path(path)==logical else original(path)
        with patch.object(W.PW,'resolve',retarget):
            writer.submit_accepted(str(self.source),str(logical),generation);writer.requests.join()
        row,=writer.collect();self.assertIsNone(row['error'])
        self.assertEqual(row['source'],str(self.source));self.assertEqual(row['target'],str(physical))
        self.assertTrue(W.saved_generation(physical,generation));self.assertFalse(logical.exists())

    def lease(self, **changes):
        return dict(dict(job=str(self.source), token='unique-writer-generation',
                         writer_released=True, gui_complete=True), **changes)

    def test_seal_requires_both_writer_and_gui_release(self):
        for changes in ({'writer_released': False}, {'gui_complete': False}, {'job': 'foreign'}):
            with self.assertRaises(ValueError):
                W.seal_finished(self.source, self.lease(**changes))
        self.assertFalse((self.source/W.SEAL).exists())

    def test_archive_rejects_changed_sealed_payload(self):
        W.seal_finished(self.source, self.lease())
        (self.source/'final.npy').write_bytes(b'late writer')
        with self.assertRaises(ValueError):
            W.archive_finished(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_durable_recovery_survives_working_directory_removal(self):
        import shutil
        token = W.seal_finished(self.source, self.lease())
        recovery = self.root/'recovery'/'AC9_ClothSim_jobs'/self.source.name
        W.archive_finished(self.source, recovery)
        shutil.rmtree(self.source)
        self.assertTrue(W.saved_generation(recovery, token))
        W.archive_finished(recovery, self.target)
        self.assertTrue(W.saved_generation(self.target, token))
        self.assertEqual(W.archived_path(recovery), str(self.target))
        self.assertFalse(W.saved_generation(self.target, 'other-generation'))


if __name__ == '__main__':
    unittest.main()
