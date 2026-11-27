"""Real Windows Blender host: preserve every result at long archive paths.

blender --factory-startup --python test_job_workspace_long_paths.py -- <parent> <out.json>
Synthetic files only; no user scene, solver, GPU or preference writes.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import bpy

ARGS = sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,ARGS[0])
from ac9_cloth_sim.core import job_workspace as W
OUT = Path(ARGS[1]); MEASURES = []


def raw(path):
    value = str(Path(path).absolute())
    return Path(chr(92)*2+'?'+chr(92)+value) if os.name=='nt' and not value.startswith(chr(92)*2+'?') else Path(value)


class LongJobPaths(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='ac9_job_paths_')).resolve()

    def tearDown(self):
        assert self.root.is_absolute() and self.root.name.startswith('ac9_job_paths_')
        shutil.rmtree(raw(self.root))

    def directory(self,length,label):
        path = self.root/label
        while len(str(path))<length:
            remaining = length-len(str(path))-1
            path = path.with_name(path.name+'x') if remaining==0 else path/('x'*min(70,remaining))
        raw(path).mkdir(parents=True)
        return path

    def job(self,length=211,target_long=True):
        parent = self.directory(length-9,'source')
        source = parent/'job_abcd'; raw(source).mkdir()
        for name in W.REQUIRED:
            raw(source/name).write_bytes(b'{}' if name.endswith('.json') else b'payload')
        deep = source/'s1'/('x'*48+'.bin'); raw(deep.parent).mkdir()
        raw(deep).write_bytes(b'actual additional result\x00')
        raw(source/'job.json').write_text(json.dumps(dict(dir=str(source),stages=[])))
        base = self.directory(245,'target') if target_long else self.root/'target'
        target = base/'AC9_ClothSim_jobs'/source.name
        return source,target,deep

    def test_complete_inventory_archive_readback_and_idempotent_retry(self):
        source,target,deep = self.job()
        rel = deep.relative_to(source).as_posix()
        self.assertIn(rel,W._names(source)); self.assertIn(rel,W._inventory(source))
        W._write(source/W.PLAN,dict(recovery=str(source),target=str(target)))
        lease = dict(job=str(source),writer_released=True,gui_complete=True,token='long-generation')
        W.seal_finished(source,lease)
        self.assertEqual(W.archive_finished(source,target),str(target))
        self.assertTrue(W.saved_generation(target,lease['token']))
        self.assertEqual(W.archived_path(source),str(target))
        self.assertEqual(raw(target/rel).read_bytes(),raw(deep).read_bytes())
        receipt = json.loads(raw(target/W.RECEIPT).read_text())
        self.assertEqual(receipt['target'],str(target))
        self.assertFalse(any('?' in key for key in receipt['saved_files']))
        self.assertEqual(json.loads(raw(target/'job.json').read_text())['dir'],str(target))
        self.assertEqual(W.archive_finished(source,target),str(target))
        MEASURES.append(dict(source_length=len(str(source)),deep_length=len(str(deep)),
            target_length=len(str(target)),additional_file=True,inventory=len(receipt['saved_files'])))

    def test_long_foreign_destination_remains_byte_identical(self):
        source,target,deep = self.job()
        raw(target).mkdir(parents=True); marker=raw(target/'valuable.bin'); marker.write_bytes(b'foreign')
        with self.assertRaises(FileExistsError): W.archive_finished(source,target)
        self.assertEqual(marker.read_bytes(),b'foreign')
        self.assertFalse(raw(target/W.RECEIPT).exists())

    def test_cancel_removes_only_owned_long_partial_and_keeps_source(self):
        source,target,deep = self.job()
        before = W._inventory(source)
        with self.assertRaises(W.ExportCancelled): W.archive_finished(source,target,cancelled=lambda:True)
        self.assertFalse(raw(target).exists())
        self.assertEqual(W._inventory(source),before)

    def test_short_root_with_long_descendant_is_not_omitted(self):
        source,target,deep = self.job(150,False)
        nested = deep.parent/('y'*110)/'final.bin'; raw(nested.parent).mkdir()
        raw(nested).write_bytes(b'long descendant')
        W.archive_finished(source,target)
        self.assertEqual(raw(target/nested.relative_to(source)).read_bytes(),b'long descendant')

    def test_sealed_recovery_queue_revalidates_long_plan_and_publishes(self):
        source,target,deep = self.job()
        recovery=self.directory(245,'recovery')/'AC9_ClothSim_jobs'/source.name
        generation='queued-long-generation'
        W._write(source/W.PLAN,dict(version=1,generation=generation,
            working=str(source),recovery=str(recovery),target=str(target)))
        W.seal_finished(source,dict(job=str(source),token=generation,writer_released=True,gui_complete=True))
        W.archive_finished(source,recovery)
        queue=W.ArchiveQueue()
        self.assertTrue(queue.submit_accepted(str(recovery),str(target),generation))
        queue.requests.join()
        rows=queue.collect()
        self.assertEqual(len(rows),1); self.assertIsNone(rows[0]['error'],rows)
        self.assertEqual(rows[0]['source'],str(recovery)); self.assertEqual(rows[0]['target'],str(target))
        self.assertTrue(W.saved_generation(target,generation))
        self.assertEqual(raw(target/deep.relative_to(source)).read_bytes(),raw(deep).read_bytes())


suite=unittest.defaultTestLoader.loadTestsFromTestCase(LongJobPaths)
result=unittest.TextTestRunner().run(suite)
OUT.write_text(json.dumps(dict(ok=result.wasSuccessful(),tests=result.testsRun,measures=MEASURES,
    failures=[str(case)+'\n'+error for case,error in result.failures],
    errors=[str(case)+'\n'+error for case,error in result.errors]),indent=2),encoding='utf8')
if not result.wasSuccessful(): raise RuntimeError('Long result archive paths failed in Blender')
