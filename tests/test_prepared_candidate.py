import io,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS, ModuleType
from unittest.mock import patch
import numpy as np
import importlib.util

# Import this core module as a small package without importing Blender registration.
ROOT=Path(__file__).resolve().parents[1]
PKG='_prepared_candidate_test_package'
for name,path in [(PKG,ROOT),(PKG+'.core',ROOT/'core'),(PKG+'.solver',ROOT/'solver')]:
 module=ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module
import importlib
C=importlib.import_module(PKG+'.core.prepared_candidate')
P=C.protocol

class CandidateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='ac9_candidate_fixture_');self.root=Path(self.temp.name)
  self.idx=self.root/'idx.npz';self.avatar=self.root/'avatar.npz';self.cfg=self.root/'config.json'
  np.savez(self.idx,Xinit=np.zeros((4,3)),Xclo=np.zeros((4,3)));np.savez(self.avatar,co=np.ones((3,3)))
  self.cfg.write_text(json.dumps(dict(native_three=True,live=True,iterations=20)))
  self.proc=NS(stdin=io.StringIO(),poll=lambda:None,pid=123)
  self.proc.kill=lambda:setattr(self.proc,'poll',lambda:1)
  fake_job=NS(name='fake-job',assign=lambda process:None,active=lambda:1 if self.proc.poll()is None else 0,close=lambda:None,terminate=lambda:None)
  self.job=patch.object(C.OwnedProcess,'job_type',lambda *args:fake_job);self.job.start()
  self.spawn=patch.object(C.subprocess,'Popen',return_value=self.proc);self.spawn.start()
  self.source=patch.object(P,'source_id',lambda *args:'source');self.source.start()
  self.c=C.Candidate(sys.executable,{},self.idx,self.avatar,self.cfg,3)
  self.ready()
 def ready(self):
  cfg=json.loads(self.cfg.read_text());fingerprint=P.fingerprint(self.idx,self.avatar,cfg,P.environment_id({}),implementation='source')
  P.write(self.c.status_path,dict(version=1,token=self.c.token,generation=3,phase='prepared',dependencies={},implementation='source',fingerprint=fingerprint))
 def tearDown(self):
  self.spawn.stop();self.source.stop();self.job.stop()
  import shutil
  assert self.c.root.resolve().parent==Path(tempfile.gettempdir()).resolve() and self.c.root.name.startswith('ac9_prepared_candidate_')
  if self.c.root.exists():shutil.rmtree(self.c.root)
  self.temp.cleanup()
 def match(self,**kw):
  args=dict(python=sys.executable,environment={},idx=self.idx,avatar=self.avatar,config_path=self.cfg,generation=3);args.update(kw)
  return self.c.matches(**args)
 def test_snapshot_is_independent_and_fresh_inputs_must_match(self):
  self.assertTrue(self.match())
  np.savez(self.idx,Xinit=np.ones((4,3)));self.assertFalse(self.match())
  self.assertTrue(np.array_equal(np.load(self.c.root/'scene.idx.npz')['Xinit'],np.zeros((4,3))))
 def test_interpreter_environment_generation_and_source_invalidate(self):
  for kwargs in [dict(python=str(self.root/'different.exe')),dict(environment={'AC9_DRESS':'1'}),dict(generation=4)]:
   self.assertFalse(self.match(**kwargs))
  with patch.object(P,'source_id',lambda *args:'newsource'):self.assertFalse(self.match())
 def test_status_corruption_and_exit_are_not_ready(self):
  for value in [[],{'version':1},dict(version=1,token=self.c.token,generation=3,phase='failed')]:
   P.write(self.c.status_path,value);self.assertFalse(self.match())
  self.ready();self.proc.poll=lambda:1;self.assertFalse(self.match())
 def test_slot_is_consumed_once_and_clear_invalidates_generation(self):
  slot=C.Slot();slot.generation=3;slot.candidate=self.c
  args=(sys.executable,{},self.idx,self.avatar,self.cfg)
  self.assertIs(slot.take(*args),self.c);self.assertIsNone(slot.take(*args))
  self.c.start(self.idx,self.avatar,self.cfg,self.root/'actual')
  command=json.loads(self.proc.stdin.getvalue());self.assertEqual(command['generation'],3)
  with self.assertRaises(ValueError):self.c.start(self.idx,self.avatar,self.cfg,self.root/'actual')
  slot.candidate=self.c;slot.clear();self.assertEqual(slot.generation,4);self.assertTrue(self.proc.stdin.closed)
 def test_root_is_retained_while_running_and_collected_after_exit(self):
  self.assertFalse(self.c.cleanup());self.assertTrue(self.c.root.exists())
  self.proc.poll=lambda:0;self.assertTrue(self.c.cleanup());self.assertFalse(self.c.root.exists())
 def test_changed_marker_refuses_cleanup(self):
  self.proc.poll=lambda:0;P.write(self.c.root/'owner.json',dict(root=str(self.root),token=self.c.token))
  with self.assertRaises(ValueError):self.c.cleanup()
  self.assertTrue(self.c.root.exists())
 def test_constructor_failure_removes_its_own_root(self):
  roots=[];original=C.tempfile.mkdtemp
  def create(*args,**kwargs):
   root=original(*args,**kwargs);roots.append(Path(root));return root
  with patch.object(C.tempfile,'mkdtemp',create),patch.object(C.shutil,'copy2',side_effect=OSError('copy failed')):
   with self.assertRaises(OSError):C.Candidate(sys.executable,{},self.idx,self.avatar,self.cfg,4)
  self.assertEqual(len(roots),1);self.assertFalse(roots[0].exists())
 def test_snapshot_positions_change_only_owned_input(self):
  self.proc.poll=lambda:0;self.c.cleanup()
  before=self.idx.read_bytes();positions=np.ones((4,3))*7
  self.c=C.Candidate(sys.executable,{},self.idx,self.avatar,self.cfg,4,positions=positions)
  self.assertEqual(self.idx.read_bytes(),before)
  self.assertTrue(np.array_equal(np.load(self.c.root/'scene.idx.npz')['Xinit'],positions))
  self.assertTrue(np.array_equal(np.load(self.c.root/'scene.idx.npz')['Xclo'],positions))
  self.assertFalse(self.match(generation=4))
 def test_kill_signals_job_without_subprocess_wait(self):
  from unittest.mock import Mock
  terminate=Mock();self.c.process.job.terminate=terminate
  with patch.object(C.subprocess,'run',side_effect=AssertionError('blocking subprocess')):
   self.c.process.kill()
  terminate.assert_called_once()
 def loading(self):
  row=json.loads(self.c.status_path.read_text());row['phase']='loading';P.write(self.c.status_path,row)
 def pending(self):
  return C.PendingStart(self.c,sys.executable,{},self.idx,self.avatar,self.cfg,self.root/'actual',3)
 def test_loading_match_keeps_full_identity_guard(self):
  self.loading();self.assertFalse(self.match());self.assertTrue(self.match(allow_loading=True))
  np.savez(self.idx,Xinit=np.ones((4,3)));self.assertFalse(self.match(allow_loading=True))
 def test_pending_waits_for_ready_and_sends_exactly_once(self):
  self.loading();pending=self.pending()
  self.assertEqual(pending.poll(),'waiting');self.assertEqual(self.proc.stdin.getvalue(),'')
  self.ready();self.assertEqual(pending.poll(),'started');self.assertEqual(pending.poll(),'started')
  self.assertEqual(len(self.proc.stdin.getvalue().splitlines()),1)
 def test_pending_cancel_wins_over_ready(self):
  pending=self.pending();pending.cancel();self.assertEqual(pending.poll(),'canceled')
  self.assertTrue(self.proc.stdin.closed)
 def test_pending_changed_source_rejects_before_send(self):
  pending=self.pending()
  with patch.object(P,'source_id',lambda *args:'changed'):self.assertEqual(pending.poll(),'rejected')
  self.assertEqual(self.proc.stdin.getvalue(),'')
 def test_pending_uncertain_write_is_not_retried(self):
  pending=self.pending()
  with patch.object(self.c,'start',side_effect=BrokenPipeError('injected'))as start:
   self.assertEqual(pending.poll(),'uncertain');self.assertEqual(pending.poll(),'uncertain')
  start.assert_called_once()
 def test_before_first_status_only_holds_numeric_match_until_engine_ready(self):
  self.c.status_path.unlink();self.assertTrue(self.match(allow_loading=True));self.assertFalse(self.match())
  pending=self.pending();self.assertEqual(pending.poll(),'waiting');self.assertEqual(self.proc.stdin.getvalue(),'')
  self.ready()
  with patch.object(P,'source_id',lambda *args:'changed-engine'):self.assertEqual(pending.poll(),'rejected')
  self.assertEqual(self.proc.stdin.getvalue(),'')

if __name__=='__main__':unittest.main()
