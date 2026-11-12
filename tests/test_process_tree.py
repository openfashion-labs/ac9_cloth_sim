import json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from solver.process_tree import OwnedProcess,Accounting,BasicLimits,ExtendedLimits
import ctypes

@unittest.skipUnless(os.name=='nt','Windows process ownership')
class ProcessTreeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='ac9_job_tree_test_');self.root=Path(self.temp.name)
  self.script=self.root/'parent.py';self.status=self.root/'child.json'
  solver=Path(__file__).resolve().parents[1]/'solver'
  child="import os,json,sys;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps(dict(pid=os.getpid(),ppid=os.getppid())));sys.stdin.readline()"
  self.script.write_text("import sys,subprocess\nsys.path.insert(0,"+repr(str(solver))+")\nfrom process_tree import join_owned_job\njoin_owned_job()\np=subprocess.Popen([sys.executable,'-c',"+repr(child)+",sys.argv[1]])\nraise SystemExit(p.wait())\n",encoding='utf8')
  self.p=OwnedProcess([sys.executable,str(self.script),str(self.status)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
  until=time.monotonic()+5
  while not self.status.exists():
   self.assertIsNone(self.p.poll());self.assertLess(time.monotonic(),until);time.sleep(.01)
  self.child=json.loads(self.status.read_text());self.assertNotEqual(self.child['pid'],self.p.pid)
  self.assertGreaterEqual(self.p.job.active(),2)
 def tearDown(self):
  if self.p.poll()is None:self.p.kill();self.p.wait(timeout=5)
  if self.p.stdin:self.p.stdin.close()
  if self.p.stderr:self.p.stderr.close()
  self.temp.cleanup()
 def test_normal_exit_waits_for_entire_job(self):
  self.p.stdin.write('\n');self.p.stdin.flush();self.assertEqual(self.p.wait(timeout=5),0)
  self.assertIsNone(self.p.job.handle)
 def test_parent_only_exit_terminates_remaining_worker_before_completion(self):
  self.p.process.kill();self.p.process.wait(timeout=5)
  self.assertGreaterEqual(self.p.job.active(),1)
  self.assertIsNone(self.p.poll())
  self.assertNotEqual(self.p.wait(timeout=5),0);self.assertIsNone(self.p.job.handle)
 def test_kill_is_a_nonwaiting_kernel_request(self):
  begin=time.perf_counter();self.p.kill();duration=time.perf_counter()-begin
  self.assertLess(duration,.1);self.assertNotEqual(self.p.wait(timeout=5),0)
 def test_sdk_structure_sizes(self):
  self.assertEqual(ctypes.sizeof(Accounting),48)
  self.assertEqual(ctypes.sizeof(BasicLimits),64)
  self.assertEqual(ctypes.sizeof(ExtendedLimits),144)

if __name__=='__main__':unittest.main()
