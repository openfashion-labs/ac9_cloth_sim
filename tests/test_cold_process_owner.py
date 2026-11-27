"""The actual cold launch must not leave a worker running after its host dies.

CPU-only private processes, extracted launch code, no Blender or user scene.
"""
import ast
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid

ADDON=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ADDON/'solver'))
from process_tree import ENV_KEY,OwnedProcess,join_owned_job


def write(area,name,value):
    temp=area/(name+'.tmp');temp.write_text(json.dumps(value));temp.replace(area/name)


def worker(area,token):
    join_owned_job()
    write(area,'ready.json',dict(pid=os.getpid(),token=token,owned=bool(os.environ.get(ENV_KEY))))
    with (area/'pulse').open('w') as stream:
        until=time.monotonic()+8
        while time.monotonic()<until and not (area/'finish').exists():
            stream.write('pulse\n');stream.flush();time.sleep(.04)


def host(area,token,python):
    source=ADDON/'dress.py'
    module=ast.parse(source.read_text(encoding='utf8'))
    cls=next(n for n in module.body if isinstance(n,ast.ClassDef) and n.name=='AC9_OT_SimDress')
    stage=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_next_stage')
    launch=next(n for n in ast.walk(stage) if isinstance(n,ast.FunctionDef) and n.name=='launch')
    env=dict(os.environ);env.pop(ENV_KEY,None)
    with (area/'solver_stdout.txt').open('w') as log:
        instance=SimpleNamespace(python=python,log=log,_native_io=None)
        ns=dict(_trace=lambda *a,**k:None,self=instance,os=os,d=str(area),subprocess=subprocess,
            RUN_STAGES=str(Path(__file__).resolve()),args=['worker',str(area),token,'s1','s2'],env=env,
            _prepared_candidate=SimpleNamespace(OwnedProcess=OwnedProcess))
        exec(compile(ast.Module(body=[launch],type_ignores=[]),str(source),'exec'),ns)
        process=ns['launch']()
        write(area,'host.json',dict(pid=os.getpid(),worker_launcher=process.pid))
        process.wait(timeout=12)


@unittest.skipUnless(os.name=='nt','Windows process ownership')
class ColdOwner(unittest.TestCase):
    def check_python(self,python):
        with tempfile.TemporaryDirectory(prefix='ac9_cold_owner_') as temp:
            area=Path(temp);token=uuid.uuid4().hex
            env=dict(os.environ,TEMP=temp,TMP=temp);env.pop(ENV_KEY,None)
            parent=subprocess.Popen([sys._base_executable,str(Path(__file__).resolve()),'host',temp,token,python],
                env=env,creationflags=subprocess.CREATE_NO_WINDOW)
            dll=ctypes.WinDLL('kernel32',use_last_error=True)
            dll.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];dll.OpenProcess.restype=wintypes.HANDLE
            dll.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];dll.WaitForSingleObject.restype=wintypes.DWORD
            dll.CloseHandle.argtypes=[wintypes.HANDLE];dll.CloseHandle.restype=wintypes.BOOL
            handle=None
            try:
                until=time.monotonic()+6
                while not all((area/name).is_file() for name in ('ready.json','host.json','pulse')):
                    self.assertIsNone(parent.poll());self.assertLess(time.monotonic(),until);time.sleep(.02)
                ready=json.loads((area/'ready.json').read_text())
                self.assertEqual(ready['token'],token)
                self.assertEqual(json.loads((area/'host.json').read_text())['pid'],parent.pid)
                # Keep the exact process handle before killing the private host;
                # a reused PID can never become this cleanup target.
                handle=dll.OpenProcess(0x00100000,False,ready['pid'])
                self.assertTrue(handle)
                parent.kill();parent.wait(timeout=3)
                self.assertEqual(dll.WaitForSingleObject(handle,1500),0,'cold worker outlived its owner')
                self.assertTrue(ready['owned'])
            finally:
                (area/'finish').touch()
                if parent.poll() is None:parent.kill();parent.wait(timeout=3)
                if handle:
                    self.assertEqual(dll.WaitForSingleObject(handle,9500),0,'private worker cleanup failed')
                    dll.CloseHandle(handle)

    def test_base_python(self):self.check_python(sys._base_executable)
    def test_venv_python(self):self.check_python(sys.executable)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='worker':worker(Path(sys.argv[2]),sys.argv[3])
    elif len(sys.argv)>1 and sys.argv[1]=='host':host(Path(sys.argv[2]),sys.argv[3],sys.argv[4])
    else:unittest.main()
