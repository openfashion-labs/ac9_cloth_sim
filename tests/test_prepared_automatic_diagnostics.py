"""Exercise production automatic-preparation decisions without starting a GPU worker."""
import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
import numpy as np


class AutomaticDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.job=Path(self.temp.name);(self.job/'s1').mkdir()
        (self.job/'job.json').write_text(json.dumps(dict(stages=['live'])))
        (self.job/'s1/config.json').write_text(json.dumps(dict(native_three=True,live=True)))
        (self.job/'s1/run_log.json').write_text(json.dumps(dict(frame_s=[.02])))
        self.g=NS(mode='OBJECT');self.shape=np.zeros((3,3));self.spec='same spec'
        self.events=[];self.offers=[];self.cancels=[];self.slot=NS(candidate=None)
        self.context=NS(scene=NS(ac9_cloth_sim=NS(native_three=True)))
        def end(reason='caller',**details):
            if self.slot.candidate is not None:self.cancels.append((reason,details))
            self.slot.candidate=None
        def offer(*args,**kwargs):
            self.slot.candidate=NS(status=lambda:dict(phase='loading',completed=2),
                                   process=NS(pid=100,poll=lambda:None,error=None))
            self.offers.append(kwargs);return True
        self.scope=dict(os=NS(environ={},path=os.path),json=json,np=np,hashlib=hashlib,time=time,
            bpy=NS(data=NS(objects={'garment':self.g})),store=NS(get=lambda ctx:({'ok':True},None),text=lambda ctx:NS(as_string=lambda:self.spec)),
            garment_name=lambda spec:'garment',job_of=lambda g:str(self.job),frozen=lambda g:True,
            reset_pieces=lambda g:[],job_last_frame=lambda job:self.shape,standby_enabled=lambda ctx:True,
            newton_python=lambda ctx:'python',solver_process_env=lambda *a:{'test':'value'},shown_shape=lambda *a:self.shape,
            _standby_key=lambda *a:('python',(),('native_three',True),1),_session_enabled=lambda *a:False,
            standby_end=end,_prepared_end=end,
            _PREPARED=self.slot,_PREPARED_TARGET=dict(key=None,attempted=None,idle=0),_PRELOAD={},
            STANDBY_IDLE_S=900,reap_retired=lambda:None,retired_running=lambda:False,_gpu_free_mb=lambda:10000,
            STANDBY_MIN_FREE_MB=1024,_prepared_offer=offer,_trace=lambda event,**details:self.events.append((event,details)))
        tree=ast.parse((Path(__file__).resolve().parents[1]/'dress.py').read_text(encoding='utf8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_prepared_automatic')
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<production automatic>','exec'),self.scope)
    def run_auto(self):return self.scope['_prepared_automatic'](self.context)
    def test_unchanged_input_retains_same_candidate(self):
        self.assertTrue(self.run_auto());candidate=self.slot.candidate
        for _ in range(4):self.assertTrue(self.run_auto())
        self.assertIs(candidate,self.slot.candidate);self.assertEqual(len(self.offers),1);self.assertEqual(self.cancels,[])
    def test_changed_shape_is_identified_and_rebuilt(self):
        self.run_auto();self.shape[0,0]=1.;self.run_auto()
        reason,details=self.cancels[0];self.assertEqual(reason,'automatic_key_changed');self.assertEqual(details['changed'],['shape'])
        self.assertEqual(len(self.offers),2)
    def test_spec_change_is_distinguished_from_shape(self):
        self.run_auto();self.spec='new spec';self.run_auto()
        self.assertEqual(self.cancels[0][1]['changed'],['spec'])
    def test_read_error_reports_step_and_type(self):
        self.run_auto();(self.job/'s1/run_log.json').unlink();self.assertFalse(self.run_auto())
        reason,details=self.cancels[0];self.assertEqual(reason,'automatic_exception')
        self.assertEqual(details['step'],'completed_job');self.assertEqual(details['exception'],'FileNotFoundError')
    def test_failed_worker_is_identified(self):
        self.run_auto();self.slot.candidate.status=lambda:dict(phase='failed',reason='test failure')
        self.assertTrue(self.run_auto());self.assertEqual(self.cancels[0][0],'candidate_failed')
        self.assertEqual(self.cancels[0][1]['worker_reason'],'test failure')
    def test_ineligible_object_is_identified(self):
        self.run_auto();self.g.mode='EDIT';self.assertFalse(self.run_auto())
        self.assertEqual(self.cancels[0][0],'garment_ineligible');self.assertEqual(self.cancels[0][1]['object_mode'],'EDIT')


if __name__=='__main__':unittest.main()
