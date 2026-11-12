import ast,importlib,io,json,sys,tempfile,unittest
from pathlib import Path
from types import ModuleType,SimpleNamespace as NS
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1];PKG='_live_session_test_package'
for name,path in [(PKG,ROOT),(PKG+'.core',ROOT/'core'),(PKG+'.solver',ROOT/'solver')]:
 module=ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module
C=importlib.import_module(PKG+'.core.live_session')
IO=importlib.import_module(PKG+'.core.native_three_io')

class Process:
 def __init__(self,*args,**kwargs):self.pid=123;self.code=None;self.stdin=io.StringIO();self.kills=0
 def poll(self):return self.code
 def kill(self):self.kills+=1;self.code=1

class SessionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.idx=self.root/'idx.npz';self.avatar=self.root/'avatar.npz';self.cfg=self.root/'config.json'
  np.savez(self.idx,Xinit=np.zeros((4,3)),Xclo=np.zeros((4,3)));np.savez(self.avatar,co=np.zeros((3,3)))
  self.cfg.write_text(json.dumps(dict(native_three=True,live=True)))
  self.spawn=patch.object(C,'OwnedProcess',Process);self.spawn.start();self.addCleanup(self.spawn.stop)
  self.code=patch.object(C.protocol,'code_id',return_value='source');self.code.start();self.addCleanup(self.code.stop)
  self.session=C.Session(sys.executable,{},self.cfg)
  def cleanup():self.session.process.code=1;self.session.cleanup()
  self.addCleanup(cleanup)
 def job(self):
  stage=self.root/('stage'+str(self.session.generation));stage.mkdir()
  return self.session.submit(self.idx,self.avatar,self.cfg,stage)
 def complete(self,job,**overrides):
  row={k:job.command[k]for k in ('session_token','job_token','generation')}
  row.update(version=1,phase='complete',code=0);row.update(overrides)
  C.protocol.write(job.receipt,row)
 def test_job_done_is_not_process_exit_and_requires_reader_release(self):
  job=self.job();self.complete(job);self.assertEqual(job.poll(),0)
  self.assertIsNone(self.session.process.poll());self.assertIs(self.session.lease,job)
  with self.assertRaises(ValueError):self.job()
  job.release();self.assertIsNone(self.session.lease)
 def test_stale_handle_cannot_kill_a_new_job(self):
  old=self.job();self.complete(old);old.release();new=self.job();old.kill()
  self.assertEqual(self.session.process.kills,0);self.assertIsNone(new.poll())
  new.kill();self.assertEqual(self.session.process.kills,1)
 def test_exit_zero_without_receipt_is_failure(self):
  job=self.job();self.session.process.code=0
  self.assertEqual(job.poll(),1);self.assertTrue(self.session.poisoned)
 def test_failed_cleanup_ack_waits_for_whole_process_exit(self):
  job=self.job();self.complete(job,code=1)
  self.session.process.kill=lambda:None
  self.assertIsNone(job.poll());self.assertTrue(self.session.poisoned)
  self.session.process.code=1;self.assertEqual(job.poll(),1)
 def test_wrong_tokens_versions_and_generations_are_not_completion(self):
  job=self.job()
  for changes in [dict(job_token='wrong'),dict(session_token='wrong'),dict(generation=0),dict(version=9),dict(code=True)]:
   self.complete(job,**changes);self.assertIsNone(job.poll())
  self.complete(job);self.assertEqual(job.poll(),0)
 def test_early_reader_release_is_refused(self):
  job=self.job()
  with self.assertRaises(ValueError):job.release()
 def test_pipe_header_excludes_paths_and_matches_atomic_request(self):
  job=self.job();header=json.loads(self.session.process.stdin.getvalue())
  self.assertNotIn('args',header);self.assertLess(len(self.session.process.stdin.getvalue()),512)
  request=C.read(self.session.root/(job.command['job_token']+'.request.json'))
  self.assertEqual(request,job.command)
 def test_uncertain_send_keeps_lease_and_forbids_reuse(self):
  class Broken:
   def write(self,value):raise OSError('uncertain write')
   def close(self):pass
  self.session.process.stdin=Broken();job=self.job()
  self.assertEqual(job.error,'uncertain write');self.assertIs(self.session.lease,job);self.assertTrue(self.session.poisoned)
  with self.assertRaises(ValueError):self.job()
 def test_changed_compile_variant_is_not_compatible(self):
  self.cfg.write_text(json.dumps(dict(native_three=True,live=True,membrane='bw')))
  self.assertFalse(self.session.compatible(sys.executable,{},self.cfg))
 def test_close_requests_stop_without_killing_another_session(self):
  job=self.job();self.session.close()
  self.assertTrue(Path(job.command['args'][3],'stop').exists());self.assertTrue(self.session.process.stdin.closed)
 def test_keep_ready_off_does_not_stop_the_current_job(self):
  job=self.job();self.session.finish_without_reuse()
  self.assertFalse(Path(job.command['args'][3],'stop').exists())
  self.assertTrue(self.session.process.stdin.closed);self.assertEqual(self.session.process.kills,0)
  self.assertTrue(self.session.poisoned)
 def test_frontend_closes_runtime_before_returning_lease(self):
  job=self.job();runtime=IO.FrontendRuntime(self.cfg);runtime.launched(job)
  self.complete(job);self.assertTrue(runtime.owner.directory.exists())
  self.assertTrue(runtime.close());self.assertFalse(runtime.owner.directory.exists());self.assertTrue(job.released)

class IdleRetirementTests(unittest.TestCase):
 def test_default_route_is_limited_and_explicit_zero_reverts(self):
  tree=ast.parse((ROOT/'dress.py').read_text(encoding='utf8'))
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_session_enabled')
  for flag in (None,'0','1'):
   for native in (False,True):
    for keep in (False,True):
     env={}if flag is None else {'AC9_NATIVE_SESSION':flag}
     scope=dict(os=NS(environ=env),standby_enabled=lambda ctx:keep)
     exec(compile(ast.Module(body=[fn],type_ignores=[]),'<production session selection>','exec'),scope)
     ctx=NS(scene=NS(ac9_cloth_sim=NS(native_three=native)))
     self.assertEqual(scope['_session_enabled'](ctx),flag!='0' and native and keep)
 def test_failed_background_start_is_not_repeated_every_tick(self):
  tree=ast.parse((ROOT/'dress.py').read_text(encoding='utf8'))
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_session_prepare')
  attempts=[]
  def get(*args):attempts.append(args);raise OSError('private startup failed')
  scope=dict(_SESSION_FOREGROUND=False,_SESSION_WARM_FAILURE=None,time=NS(time=lambda:10),
      _STANDBY={'used':10},STANDBY_IDLE_S=900,_wake_args=lambda ctx:('python',{},'cfg'),
      _standby_key=lambda *args:'variant',_LIVE_SESSION=None,_gpu_free_mb=lambda:10000,
      STANDBY_MIN_FREE_MB=1024,_session_get=get,_PRELOAD={})
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'<production session startup>','exec'),scope)
  for _ in range(4):scope['_session_prepare'](None)
  self.assertEqual(len(attempts),1);self.assertEqual(scope['_PRELOAD']['state'],'failed')
 def test_expiration_clears_private_candidates_and_session(self):
  tree=ast.parse((ROOT/'dress.py').read_text(encoding='utf8'))
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_session_prepare')
  for phase in (None,'loading','prepared'):
   state={'candidate':NS(phase=phase)if phase else None,'session':object()};events=[]
   def end_candidate(*args):events.append('prepared');state['candidate']=None
   def end_session():events.append('session');state['session']=None
   scope=dict(_SESSION_FOREGROUND=False,time=NS(time=lambda:1000),_STANDBY={'used':0},STANDBY_IDLE_S=900,
              _prepared_end=end_candidate,_session_end=end_session,_PRELOAD={})
   exec(compile(ast.Module(body=[fn],type_ignores=[]),'<production session idle>','exec'),scope)
   scope['_session_prepare'](None)
   self.assertEqual(events,['prepared','session']);self.assertIsNone(state['candidate']);self.assertIsNone(state['session'])
 def test_abandon_retires_actual_session_without_waiting_on_job(self):
  tree=ast.parse((ROOT/'dress.py').read_text(encoding='utf8'))
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_stop_or_retire')
  actual=object();calls=[]
  session=NS(process=actual,close=lambda:calls.append('close'))
  facade=NS(is_session_job=True,session=session)
  def retire(process,stages):calls.append((process,stages));return NS()
  timers=NS(is_registered=lambda fn:True)
  scope=dict(process_running=lambda proc:True,retire=retire,reap_retired=lambda:None,
             bpy=NS(app=NS(timers=timers)),time=NS(monotonic=lambda:1))
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'<production session abandon>','exec'),scope)
  scope['_stop_or_retire'](facade,['stage'])
  self.assertEqual(calls,['close',(actual,[])])

class RequestFileTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.header=dict(op='run',session_token='a'*32,job_token='b'*32,generation=1)
  self.command=dict(self.header,args=['idx','avatar','config','stage'],code='code',fingerprint='hash')
  self.path=self.root/('b'*32+'.request.json');C.protocol.write(self.path,self.command)
  tree=ast.parse((ROOT/'solver/live_session_worker.py').read_text(encoding='utf8'))
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='load_request')
  scope=dict(json=json);exec(compile(ast.Module(body=[fn],type_ignores=[]),'<production request reader>','exec'),scope)
  self.reader=scope['load_request']
 def read(self,header=None,generation=0):
  return self.reader(json.dumps(self.header if header is None else header),{'session_token':'a'*32},self.root,generation)
 def test_small_header_reads_matching_request(self):self.assertEqual(self.read(),self.command)
 def test_replay_and_header_mismatch_are_refused(self):
  with self.assertRaises(ValueError):self.read(generation=1)
  for changes in (dict(session_token='wrong'),dict(job_token='../escape'),dict(generation=True),dict(extra='x')):
   with self.assertRaises(ValueError):self.read(dict(self.header,**changes))
 def test_partial_and_mismatched_body_are_refused(self):
  self.path.write_text('{')
  with self.assertRaises(ValueError):self.read()
  for changes in (dict(generation=2),dict(generation=True),dict(args=['one']),dict(op='quit')):
   C.protocol.write(self.path,dict(self.command,**changes))
   with self.assertRaises(ValueError):self.read()
 def test_request_size_is_bounded(self):
  self.path.write_text(' '*65537)
  with self.assertRaises(ValueError):self.read()
  with self.assertRaises(ValueError):self.reader(' '*4097,{},self.root,0)

if __name__=='__main__':unittest.main()
