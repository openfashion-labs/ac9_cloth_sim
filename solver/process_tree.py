# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows job ownership; poll and kill never wait for another process.

Structures and constants follow Microsoft Learn's jobapi2/winnt documentation.
The worker joins before importing NumPy/Newton, closing the redirector spawn race.
"""
import ctypes as C
from ctypes import wintypes as W
import os
import subprocess
import time
import uuid

ENV_KEY='AC9_OWNED_SOLVER_JOB'


class BasicLimits(C.Structure):
    _fields_=[('process_time',C.c_longlong),('job_time',C.c_longlong),('flags',W.DWORD),
              ('min_working',C.c_size_t),('max_working',C.c_size_t),('active_limit',W.DWORD),
              ('affinity',C.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]


class ExtendedLimits(C.Structure):
    _fields_=[('basic',BasicLimits),('io',C.c_ulonglong*6),('process_memory',C.c_size_t),
              ('job_memory',C.c_size_t),('peak_process',C.c_size_t),('peak_job',C.c_size_t)]


class Accounting(C.Structure):
    _fields_=[('times',C.c_longlong*4),('faults',W.DWORD),('total',W.DWORD),
              ('active',W.DWORD),('terminated',W.DWORD)]


def api():
    dll=C.WinDLL('kernel32',use_last_error=True)
    signatures={
        'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
        'OpenJobObjectW':([W.DWORD,W.BOOL,W.LPCWSTR],W.HANDLE),
        'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
        'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p],W.BOOL),
        'AssignProcessToJobObject':([W.HANDLE,W.HANDLE],W.BOOL),
        'IsProcessInJob':([W.HANDLE,W.HANDLE,C.POINTER(W.BOOL)],W.BOOL),
        'GetCurrentProcess':([],W.HANDLE),
        'TerminateJobObject':([W.HANDLE,W.UINT],W.BOOL),
        'CloseHandle':([W.HANDLE],W.BOOL)}
    for name,(arguments,result)in signatures.items():
        function=getattr(dll,name);function.argtypes=arguments;function.restype=result
    return dll


def checked(value):
    if not value:raise C.WinError(C.get_last_error())
    return value


def assign(dll,job,process):
    member=W.BOOL()
    checked(dll.IsProcessInJob(process,job,C.byref(member)))
    if not member.value:checked(dll.AssignProcessToJobObject(job,process))


class WindowsJob:
    def __init__(self):
        self.dll=api();self.name='Local\\AC9_Solver_'+uuid.uuid4().hex
        self.handle=checked(self.dll.CreateJobObjectW(None,self.name))
        limits=ExtendedLimits();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
        try:checked(self.dll.SetInformationJobObject(self.handle,9,C.byref(limits),C.sizeof(limits)))
        except BaseException:self.close();raise

    def assign(self,process):assign(self.dll,self.handle,int(process._handle))

    def active(self):
        if not self.handle:return 0
        row=Accounting()
        checked(self.dll.QueryInformationJobObject(self.handle,1,C.byref(row),C.sizeof(row),None))
        return int(row.active)

    def terminate(self):
        if self.handle:checked(self.dll.TerminateJobObject(self.handle,1))

    def close(self):
        if self.handle:
            checked(self.dll.CloseHandle(self.handle));self.handle=None


def join_owned_job():
    name=os.environ.get(ENV_KEY)
    if os.name!='nt' or not name:return
    if not name.startswith('Local\\AC9_Solver_'):raise ValueError('Invalid solver job ownership')
    dll=api();handle=checked(dll.OpenJobObjectW(0x0001|0x0004,False,name))
    try:assign(dll,handle,dll.GetCurrentProcess())
    finally:checked(dll.CloseHandle(handle))


class OwnedProcess:
    job_type=WindowsJob

    def __init__(self,*args,**kwargs):
        self.job=self.job_type()if os.name=='nt'else None
        self.error=None;self.process=None;self.abnormal=False
        if self.job is not None:kwargs['env']=dict(kwargs.get('env')or os.environ,**{ENV_KEY:self.job.name})
        try:self.process=subprocess.Popen(*args,**kwargs)
        except BaseException:
            if self.job is not None:self.job.close()
            raise
        if self.job is not None:
            try:self.job.assign(self.process)
            except OSError as exc:
                # Keep ownership after a spawn even if association fails.
                self.error=str(exc);self.abnormal=True
                try:self.kill()
                except OSError:pass

    def __getattr__(self,name):return getattr(self.process,name)

    def poll(self):
        code=self.process.poll()
        if code is None:return None
        if self.job is not None and self.job.active():
            self.abnormal=True
            self.kill()
            return None
        if self.job is not None:self.job.close()
        return (code or 1)if self.abnormal else code

    def kill(self):
        try:
            if self.job is not None:self.job.terminate()
            if self.process.poll()is None:self.process.kill()
        except OSError as exc:
            self.error=str(exc)
            raise

    def wait(self,timeout=None):
        deadline=None if timeout is None else time.monotonic()+timeout
        while True:
            code=self.poll()
            if code is not None:return code
            if deadline is not None and time.monotonic()>=deadline:
                raise subprocess.TimeoutExpired(self.process.args,timeout)
            time.sleep(.01)
