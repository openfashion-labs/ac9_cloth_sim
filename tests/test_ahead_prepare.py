# SPDX-License-Identifier: GPL-3.0-or-later
"""Private CPU preparation protocol: ownership, stale replies and nonblocking retirement."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('prepare_test',ROOT/'ahead_prepare.py')
AP=importlib.util.module_from_spec(spec);spec.loader.exec_module(AP)


class WorkerProtocol(unittest.TestCase):
    def setUp(self):
        base=Path(tempfile.gettempdir())/'ac9_ahead_prepare';base.mkdir(exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='test_',dir=base));self.token='owned-token'
        (self.root/'owner.json').write_text(json.dumps(dict(schema=AP.SCHEMA,token=self.token)),encoding='utf-8')
        self.proc=NS(poll=lambda:0)
        self.task=AP.Task(self.root,self.token,self.proc,None,4,.01)
    def tearDown(self):AP._remove(self.root,self.token)
    def reply(self,**change):
        for key in ('idx','avatar','cfg'):(self.root/key).touch()
        (self.root/'stage').mkdir(exist_ok=True)
        row=dict(schema=AP.SCHEMA,token=self.token,generation=4,ok=True,key='key',prepare_s=1.,key_s=.01,worker_s=2.,phases=[],solver_env={},**{k:str(self.root/k) for k in ['idx','avatar','cfg','stage']});row.update(change)
        (self.root/'result.json').write_text(json.dumps(row),encoding='utf-8')
    def test_foreign_nonce_or_generation_is_rejected(self):
        for change in [dict(token='foreign'),dict(generation=3),dict(schema=0)]:
            self.reply(**change)
            with self.assertRaises(ValueError):self.task.read()
    def test_no_output_path_can_escape(self):
        for key in ['idx','avatar','cfg','stage']:
            self.reply(**{key:str(self.root.parent/'outside')})
            with self.assertRaises(ValueError):self.task.read()
        self.reply();self.assertEqual(self.task.read()['key'],'key')
    def test_worker_failure_does_not_become_ready(self):
        self.reply(ok=False,error='fixture failure')
        with self.assertRaisesRegex(ValueError,'fixture failure'):self.task.read()
    def test_malformed_metrics_or_failed_process_cannot_be_submitted(self):
        for key,value in [('prepare_s',None),('key_s',float('nan')),('worker_s',-1),('phases',[{}]),('solver_env',{'x':3})]:
            self.reply(**{key:value})
            with self.assertRaises(ValueError):self.task.read()
        self.reply();self.task.process=NS(poll=lambda:1)
        with self.assertRaises(ValueError):self.task.read()

    def test_retirement_checks_the_marker(self):
        AP._remove(self.root,'foreign');self.assertTrue(self.root.exists())
    def test_frame_or_garment_reference_change_invalidates_snapshot(self):
        class Garment(dict):session_uid=7
        g=Garment(ac9_job='old',ac9_job_generation='one')
        props=NS(body=NS(session_uid=4),mesh_size=6.,native_three=True,fast_live=False)
        ctx=NS(scene=NS(frame_current=10,ac9_cloth_sim=props),view_layer=NS(name='Layer'))
        self.task.owner=NS(matches=lambda *a,**kw:True);self.task.garment=g
        self.task.anchor=AP._anchor(ctx,g);self.assertTrue(self.task.matches(ctx))
        ctx.scene.frame_current=11;self.assertFalse(self.task.matches(ctx));ctx.scene.frame_current=10
        g['ac9_job_generation']='two';self.assertFalse(self.task.matches(ctx))
        g['ac9_job_generation']='one'
        for name in AP.PREPARE_SCALARS:
            old=getattr(props,name,None);setattr(props,name,'changed')
            self.assertFalse(self.task.matches(ctx),name);setattr(props,name,old)

    def test_cancel_does_not_wait_for_cpu_work(self):
        proc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.task.process=proc;t=time.perf_counter();self.task.abort()
        self.assertLess(time.perf_counter()-t,.2)
        proc.wait(timeout=5)
        deadline=time.monotonic()+3
        while self.root.exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertFalse(self.root.exists())


if __name__=='__main__':unittest.main()
