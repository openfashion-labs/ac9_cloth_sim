import copy
import io
import json
from pathlib import Path
import sys
import runpy
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np
from solver import prepared_live as P


class PreparedInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ac9_prepared_test_')
        self.root=Path(self.temp.name).resolve()
        self.real_source=P.source_id
        self.source=patch.object(P,'source_id',lambda *args:'fixed-source-and-engine');self.source.start()
        self.dependencies=patch.object(P,'dependency_manifest',lambda:{});self.dependencies.start()
        self.idx=self.root/'idx.npz';self.avatar=self.root/'avatar.npz';self.pattern=self.root/'pattern.npz'
        np.savez(self.idx,Xinit=np.zeros((4,3)),F=np.array([[0,1,2],[1,2,3]],np.int32))
        np.savez(self.avatar,body_co=np.ones((3,3)),body_tri=np.array([[0,1,2]],np.int32))
        np.savez(self.pattern,names=np.array(['piece']),V=np.zeros((4,2)))
        self.cfg=dict(native_three=True,live=True,substeps=10,iterations=20,pattern=str(self.pattern),params_file='old',grab_file='old')

    def tearDown(self):
        self.source.stop();self.dependencies.stop()
        assert self.root.is_absolute()and self.root.name.startswith('ac9_prepared_test_')
        self.temp.cleanup()

    def fingerprint(self,cfg=None,env='env'):
        return P.fingerprint(self.idx,self.avatar,self.cfg if cfg is None else cfg,env)

    def test_output_control_paths_do_not_change_physical_identity(self):
        changed=dict(self.cfg,params_file='new',grab_file='new',checkpoint_out='new',native_runtime_dir='new',native_runtime_token='new')
        self.assertEqual(self.fingerprint(),self.fingerprint(changed))
        alternate=self.root/'same-pattern-new-path.npz';np.savez(alternate,V=np.zeros((4,2)),names=np.array(['piece']))
        self.assertEqual(self.fingerprint(),self.fingerprint(dict(self.cfg,pattern=str(alternate))))

    def test_positions_connectivity_material_environment_invalidate(self):
        original=self.fingerprint()
        self.assertNotEqual(original,self.fingerprint(dict(self.cfg,iterations=21)))
        self.assertNotEqual(original,self.fingerprint(env='new-env'))
        np.savez(self.idx,Xinit=np.ones((4,3)),F=np.array([[0,1,2],[1,2,3]],np.int32))
        self.assertNotEqual(original,self.fingerprint())
        np.savez(self.idx,Xinit=np.zeros((4,3)),F=np.array([[0,1,3],[1,2,3]],np.int32))
        self.assertNotEqual(original,self.fingerprint())

    def test_nested_external_input_contents_are_fingerprinted(self):
        data=self.root/'external.npy';np.save(data,np.array([1.,2.]))
        cfg=dict(self.cfg,custom_input={'nested':[str(data)]});before=self.fingerprint(cfg)
        np.save(data,np.array([2.,1.]));self.assertNotEqual(before,self.fingerprint(cfg))

    def test_objects_and_non_native_inputs_are_ineligible(self):
        for cfg in [dict(self.cfg,native_three=False),dict(self.cfg,checkpoint_in='old.npz')]:
            with self.assertRaises(ValueError):self.fingerprint(cfg)
        np.savez(self.idx,unsafe=np.array([{}],dtype=object))
        with self.assertRaises(ValueError):self.fingerprint()

    def test_implicit_history_options_are_ineligible(self):
        for key in ['untangle','iron','experimental_fold_terminal','init_from','color_dump','watch','diag','hinges','stitch_lock']:
            with self.assertRaisesRegex(ValueError,'not audited'):self.fingerprint(dict(self.cfg,**{key:True}))

    def test_real_source_identity_is_fresh_after_edit(self):
        file=self.root/'implementation.py';file.write_text('first')
        with patch.object(P,'__file__',str(self.root/'worker.py')):
            before=self.real_source();file.write_text('second')
            self.assertNotEqual(before,self.real_source())

    def test_string_system_exit_reports_failure_and_runs_cleanup(self):
        stage=self.root/'entry';stage.mkdir();spec=self.root/'entry.json';spec.write_text(json.dumps({'stage':str(stage),'idx':'idx','avatar':'avatar','config_path':'config'}))
        states=[];cleaned=[];exit_codes=[]
        class FakeGate:
            accepted=False
            latest={'phase':'loading'}
            def __init__(self,spec):pass
            def update(self,phase,**fields):self.latest={'phase':phase,**fields};states.append(self.latest)
        def fail(*args,**kwargs):
            kwargs['init_globals']['_AC9_CLEANUP'](lambda:cleaned.append(True))
            raise SystemExit('injected compile failure')
        source=(Path(__file__).resolve().parents[1]/'solver/prepared_worker.py').read_text(encoding='utf8')
        original=(sys.stdout,sys.stderr)
        try:
            with patch.dict(sys.modules,{'prepared_live':NS(Gate=FakeGate,write=P.write),'process_tree':NS(join_owned_job=lambda:None)}),patch.object(sys,'argv',['worker',str(spec)]),patch.object(runpy,'run_path',fail),patch.object(P.os,'_exit',lambda code:exit_codes.append(code)):
                exec(compile(source,'<real prepared worker>','exec'),{'__file__':str(Path(__file__).resolve().parents[1]/'solver/prepared_worker.py'),'__name__':'__main__'})
        finally:sys.stdout,sys.stderr=original
        self.assertEqual(exit_codes,[1]);self.assertEqual(cleaned,[True])
        self.assertEqual(states[-1]['phase'],'failed');self.assertIn('injected compile failure',states[-1]['reason'])

    def test_source_change_while_waiting_refuses_before_target_write(self):
        gate,g=self.gate()
        original=P.source_id;calls=[0]
        def changed(*args):
            calls[0]+=1
            return 'fixed-source-and-engine'if calls[0]==1 else'changed-source'
        command=dict(op='start',token='1'*32,generation=1,args=['not-read']*4)
        with patch.object(P,'source_id',changed),patch.object(P.sys,'stdin',io.StringIO(json.dumps(command)+'\n')):
            with self.assertRaisesRegex(ValueError,'source changed while waiting'):gate(g)
        self.assertFalse(gate.accepted)

    def test_build_progress_reports_actual_completed_stages(self):
        gate,g=self.gate()
        for stage,number in [('imported',1),('finalized',2),('solver',3)]:
            gate.progress(stage);self.assertEqual(gate.latest['completed'],number)
            self.assertEqual(gate.latest['fingerprint'],gate.initial)
        gate.accepted=True;gate.progress('imported');self.assertEqual(gate.latest['completed'],3)

    def test_status_failure_cannot_interrupt_remaining_cleanup(self):
        stage=self.root/'notify';stage.mkdir();spec=self.root/'notify.json'
        spec.write_text(json.dumps(dict(stage=str(stage),idx='idx',avatar='avatar',config_path='config')))
        cleaned=[]
        class FakeGate:
            accepted=False
            latest={'phase':'loading'}
            def __init__(self,spec):pass
            def update(self,*args,**kwargs):raise OSError('status is unwritable')
        def fail(*args,**kwargs):
            register=kwargs['init_globals']['_AC9_CLEANUP']
            register(lambda:cleaned.append('remaining'))
            def broken():raise OSError('close failed')
            register(broken)
            raise SystemExit('worker failed')
        path=Path(__file__).resolve().parents[1]/'solver/prepared_worker.py'
        namespace={'__file__':str(path),'__name__':'test_entry'}
        original=(sys.stdout,sys.stderr)
        with patch.dict(sys.modules,{'prepared_live':NS(Gate=FakeGate,write=P.write),'process_tree':NS(join_owned_job=lambda:None)}):
            exec(compile(path.read_text(encoding='utf8'),str(path),'exec'),namespace)
        with patch.object(runpy,'run_path',fail):code=namespace['main'](str(spec))
        self.assertEqual(code,1);self.assertEqual(cleaned,['remaining'])
        self.assertEqual((sys.stdout,sys.stderr),original)

    def test_invalid_runtime_owner_cannot_truncate_existing_stdout(self):
        gate,g=self.gate();stage=self.root/'not-owned';stage.mkdir();output=stage/'stdout.txt';output.write_text('keep')
        cfg=dict(self.cfg,native_runtime_dir=str(self.root/'cache'),native_runtime_token='bad')
        cfg_path=stage/'config.json';cfg_path.write_text(json.dumps(cfg))
        command=dict(op='start',token='1'*32,generation=1,args=[str(self.idx),str(self.avatar),str(cfg_path),str(stage)],fingerprint=gate.initial)
        from solver import native_runtime_io as runtime
        with patch.dict(sys.modules,{'native_runtime_io':runtime}),patch.object(P.sys,'stdin',io.StringIO(json.dumps(command)+'\n')):
            with self.assertRaises(ValueError):gate(g)
        self.assertEqual(output.read_text(),'keep');self.assertFalse(gate.accepted)

    def gate(self):
        stage=self.root/'private';stage.mkdir(exist_ok=True)
        cfg_path=stage/'config.json';cfg_path.write_text(json.dumps(self.cfg))
        spec=dict(token='1'*32,generation=1,status=str(self.root/'status.json'),idx=str(self.idx),avatar=str(self.avatar),config=self.cfg,config_path=str(cfg_path),stage=str(stage))
        gate=P.Gate(spec)
        g=dict(NATIVE_THREE=True,log={'cfg':copy.deepcopy(self.cfg)},idx_f=str(self.idx),av_f=str(self.avatar),cfg=copy.deepcopy(self.cfg),out=str(stage),LIVEP={'path':'old'},_AC9_CLEANUP=[].append)
        return gate,g

    def test_eof_and_stale_tokens_do_not_start(self):
        for command in [None,dict(op='start',token='2'*32,generation=1),dict(op='start',token='1'*32,generation=2),dict(op='start',token='1'*32,generation=True)]:
            gate,g=self.gate();before=copy.deepcopy(g['cfg'])
            with patch.object(P.sys,'stdin',io.StringIO(''if command is None else json.dumps(command)+'\n')):
                with self.assertRaises(SystemExit if command is None else ValueError):gate(g)
            self.assertFalse(gate.accepted);self.assertEqual(g['cfg'],before)

    def test_new_job_binding_and_one_time_start(self):
        gate,g=self.gate();stage=self.root/'actual';stage.mkdir()
        cfg_path=stage/'config.json';cfg=dict(self.cfg,grab_file=str(stage/'grab.json'),params_file=str(stage/'params.json'),native_runtime_dir=str(stage/'scratch'),native_runtime_token='2'*32)
        cfg_path.write_text(json.dumps(cfg));cleanup=[];g['_AC9_CLEANUP']=cleanup.append
        command=dict(op='start',token='1'*32,generation=1,args=[str(self.idx),str(self.avatar),str(cfg_path),str(stage)],fingerprint=gate.initial)
        class Runtime:
            def __init__(self,cfg,stage):self.directory=Path(cfg['native_runtime_dir'])
            def start(self):pass
            def close(self):pass
        original=(sys.stdout,sys.stderr)
        try:
            with patch.dict(sys.modules,{'native_runtime_io':NS(WorkerRuntime=Runtime)}):
                with patch.object(P.sys,'stdin',io.StringIO(json.dumps(command)+'\n')):gate(g)
            self.assertTrue(gate.accepted);self.assertEqual(g['out'],str(stage))
            self.assertEqual(g['LIVEP']['path'],cfg['params_file'])
            self.assertEqual(g['log']['cfg'],g['cfg'])
            with self.assertRaises(ValueError):gate(g)
        finally:
            sys.stdout,sys.stderr=original
            for close in reversed(cleanup):close()

    def test_existing_result_cannot_be_overwritten(self):
        gate,g=self.gate();stage=self.root/'existing';stage.mkdir();(stage/'run_log.json').write_text('keep')
        cfg_path=stage/'config.json';cfg_path.write_text(json.dumps(self.cfg))
        command=dict(op='start',token='1'*32,generation=1,args=[str(self.idx),str(self.avatar),str(cfg_path),str(stage)],fingerprint=gate.initial)
        with patch.object(P.sys,'stdin',io.StringIO(json.dumps(command)+'\n')):
            with self.assertRaisesRegex(ValueError,'existing result'):gate(g)
        self.assertEqual((stage/'run_log.json').read_text(),'keep')


if __name__=='__main__':unittest.main()
