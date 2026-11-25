# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource release and filesystem rollback boundaries, independent of Blender/GPU."""
import contextlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PKG='_runtime_repair_test'
for name,path in ((PKG,ROOT),(PKG+'.solver',ROOT/'solver')):
    module=types.ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module
R=importlib.import_module(PKG+'.runtime_repair')
W=importlib.import_module(PKG+'.runtime_repair_worker')
P=importlib.import_module(PKG+'.solver.process_tree')


class RepairSafety(unittest.TestCase):
    def cleanup_fixture(self, directory):
        extensions=Path(directory);root=extensions/'.ac9_runtime_repair'/('a'*32)
        root.mkdir(parents=True);(root/'owner.json').write_text(json.dumps({'token':root.name}))
        (root/'stage').mkdir();(root/'stage/data').write_text('private')
        return root,{'extensions':str(extensions),'token':root.name}

    def test_cleanup_retries_sharing_error_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,spec=self.cleanup_fixture(tmp)
            canary=Path(tmp)/'live-canary';canary.write_text('untouched')
            real=W.shutil.rmtree;calls=[]
            def retry(path):
                calls.append(str(path))
                if len(calls)==1:
                    error=PermissionError('injected sharing error');error.winerror=32;raise error
                return real(path)
            with patch.object(W.shutil,'rmtree',retry),patch.object(W.time,'sleep'):
                W.cleanup_owned(root,spec)
            self.assertEqual(len(calls),2);self.assertFalse(root.exists())
            self.assertEqual(canary.read_text(),'untouched')

    def test_cleanup_rejects_unverified_publication_and_wrong_owner(self):
        for state in ('publishing','published','wrong-owner'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as tmp:
                root,spec=self.cleanup_fixture(tmp)
                if state=='wrong-owner':(root/'owner.json').write_text('{}')
                else:(root/'publication.json').write_text(json.dumps({'state':state}))
                with self.assertRaises(ValueError):W.cleanup_owned(root,spec)
                self.assertEqual((root/'stage/data').read_text(),'private')

    @unittest.skipUnless(sys.platform=='win32','Windows process ownership')
    def test_cancelled_cleanup_runs_in_owned_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,spec=self.cleanup_fixture(tmp)
            job=R.RepairJob.__new__(R.RepairJob)
            job.root=root;job.parent=root.parent;job.token=root.name
            job.process=None;job.state='cancelled';job.published=False
            start=time.perf_counter();self.assertFalse(job.cleanup())
            self.assertLess(time.perf_counter()-start,1)
            end=time.monotonic()+20
            while not job.cleanup() and time.monotonic()<end:time.sleep(.01)
            self.assertEqual(job.state,'cancelled');self.assertFalse(root.exists())
            self.assertFalse(hasattr(job,'cleanup_error'))

    def test_final_validation_cannot_be_cancelled(self):
        job=R.RepairJob.__new__(R.RepairJob)
        job.state='validating';job.published=True;job.cancelled=False
        job.process=types.SimpleNamespace(kill=lambda:self.fail('Final validation was killed'))
        self.assertFalse(job.cancellable)
        self.assertFalse(job.cancel())
        self.assertEqual(job.state,'validating')
        self.assertFalse(job.cancelled)

    @unittest.skipUnless(sys.platform=='win32','Windows session cookie')
    def test_cookie_disappears_after_owner_dies(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            code=('import sys,types,importlib,pathlib,os;'
                  'p=types.ModuleType("cookie_test");p.__path__=[sys.argv[1]];sys.modules[p.__name__]=p;'
                  'r=importlib.import_module("cookie_test.runtime_repair");'
                  'c=r.SessionCookie(pathlib.Path(sys.argv[2]));'
                  'assert c.path.exists();os._exit(7)')
            run=subprocess.run([sys.executable,'-B','-c',code,str(ROOT),str(root)],capture_output=True,timeout=20)
            self.assertEqual(run.returncode,7,run.stderr)
            self.assertFalse((root/'session.cookie').exists())

    def test_accounting_settle_does_not_call_strict_poll_early(self):
        job=R.RepairJob.__new__(R.RepairJob);calls=[]
        active=[1]
        process=types.SimpleNamespace(process=types.SimpleNamespace(poll=lambda:0),
            job=types.SimpleNamespace(active=lambda:active[0]),abnormal=False,
            poll=lambda:calls.append('strict') or 0)
        job.process=process;job._exit_seen=None
        self.assertIsNone(job.process_poll());self.assertEqual(calls,[])
        active[0]=0
        self.assertEqual(job.process_poll(),0);self.assertEqual(calls,['strict'])
        self.assertFalse(process.abnormal)
        active[0]=1;calls.clear()
        self.assertIsNone(job.process_poll(process,'_cleanup_exit_seen'))
        self.assertEqual(calls,[])
        active[0]=0
        self.assertEqual(job.process_poll(process,'_cleanup_exit_seen'),0)
        self.assertFalse(process.abnormal)

    @unittest.skipUnless(sys.platform=='win32','Windows job ownership')
    def test_live_descendant_is_rejected_and_owned_tree_ends(self):
        code=('import sys,subprocess;sys.path.insert(0,sys.argv[1]);'
              'from process_tree import join_owned_job;join_owned_job();'
              'subprocess.Popen([sys.executable,"-c","import time;time.sleep(5)"]);')
        job=R.RepairJob.__new__(R.RepairJob);job._exit_seen=None
        job.process=P.OwnedProcess([sys.executable,'-c',code,str(ROOT/'solver')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            end=time.monotonic()+8
            value=None
            while time.monotonic()<end:
                value=job.process_poll()
                if value is not None:break
                time.sleep(.01)
            self.assertEqual(value,1)
            self.assertTrue(job.process.abnormal)
            self.assertEqual(job.process.job.active(),0)
        finally:
            if job.process.process.poll() is None:job.process.kill();job.process.process.wait(timeout=5)

    def test_publish_failure_rolls_back_only_owned_entries(self):
        with tempfile.TemporaryDirectory(prefix='ac9_repair_unit_') as tmp:
            root=Path(tmp);job=R.RepairJob.__new__(R.RepairJob)
            job.root=root/'owned';job.site=root/'live';job.site.mkdir()
            job.token='owned'
            keep=job.site/'other_package.txt';keep.write_text('unchanged')
            stage=job.root/'stage/.local/lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
            for name in ('a','b'):
                (stage/name).mkdir(parents=True);(stage/name/'data').write_text(name)
            job.snapshot={};job.guard=lambda before:None;job.publish_timings={}
            job.lock_factory=lambda cookie:contextlib.nullcontext({})
            rename=Path.rename
            def fail_second(source,target):
                if source==stage/'b':raise OSError('injected rename failure')
                return rename(source,target)
            with patch.object(Path,'rename',fail_second):
                with self.assertRaises(OSError):job._publish({'stage_site':str(stage),'entries':['a','b']})
            self.assertEqual(sorted(p.name for p in job.site.iterdir()),['other_package.txt'])
            self.assertEqual(keep.read_text(),'unchanged')
            self.assertEqual((stage/'a/data').read_text(),'a');self.assertEqual((stage/'b/data').read_text(),'b')

    def test_cleanup_refuses_unverified_owner(self):
        with tempfile.TemporaryDirectory(prefix='ac9_repair_unit_') as tmp:
            job=R.RepairJob.__new__(R.RepairJob);job.parent=Path(tmp);job.token='owned'
            job.root=job.parent/job.token;job.root.mkdir();job.process=None
            (job.root/'owner.json').write_text(json.dumps({'token':'different'}))
            keep=job.root/'canary';keep.write_text('keep')
            with self.assertRaises(ValueError):job.cleanup()
            self.assertEqual(keep.read_text(),'keep')


if __name__=='__main__':unittest.main()
