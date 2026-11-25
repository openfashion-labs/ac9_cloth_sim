# SPDX-License-Identifier: GPL-3.0-or-later
"""One owned interpreter; independent job leases and fresh physical models."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid
from ..solver import prepared_live as protocol
from ..solver import preload_config
from ..solver.process_tree import OwnedProcess


def read(path):
    try:
        if path.stat().st_size > 1048576:return None
        value=json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):return None


class Job:
    """Process-like view of ONE job, never ownership of the whole session."""
    is_session_job=True
    def __init__(self,session,command):
        self.session=session;self.command=command
        self.receipt=session.root/(command['job_token']+'.json')
        self.returncode=None;self.released=False;self.sent=False;self.error=None
    @property
    def pid(self):return self.session.process.pid
    def _record_failure(self,row=None):
        if getattr(self,'failure_recorded',False):return
        self.failure_recorded=True
        try:
            protocol.write(Path(self.command['args'][3])/'session_failure.json',dict(
                job_token=self.command['job_token'],generation=self.command['generation'],
                receipt=row,status=self.session.status(),process_code=self.session.process.poll(),
                worker_stderr_tail=self.session.stderr_tail()))
        except (OSError,ValueError,KeyError):pass
    def poll(self):
        if self.returncode is not None:return self.returncode
        row=read(self.receipt)
        if row and row.get('version')==1 and all(row.get(k)==self.command[k] for k in ('session_token','job_token','generation')):
            if row.get('phase')=='complete' and type(row.get('code')) is int:
                if row['code']:
                    self.error=row.get('message') or 'Native session job failed'
                    self._record_failure(row)
                    # Cleanup/GPU failure poisons the interpreter. Do not let
                    # frontend I/O disappear until its entire process tree is gone.
                    self.session.poisoned=True
                    if self.session.process.poll() is None:self.session.process.kill()
                    if self.session.process.poll() is None:return None
                self.returncode=row['code']
                return self.returncode
        if self.session.process.poll() is not None:
            self._record_failure()
            # A process exit, including code zero, is not a job acknowledgement.
            self.session.poisoned=True;self.returncode=1
        return self.returncode
    def kill(self):
        if self.session.lease is self and not self.released and self.poll() is None:
            self.session.poisoned=True;self.session.process.kill()
    def wait(self,timeout=None):
        end=None if timeout is None else time.monotonic()+timeout
        while self.poll() is None:
            if end is not None and time.monotonic()>=end:
                raise subprocess.TimeoutExpired('Native session job',timeout)
            time.sleep(.01)
        return self.returncode
    def release(self):
        """Only after the frontend has closed readers and released runtime I/O."""
        if self.poll() is None:raise ValueError('Cannot release an unfinished job')
        if self.session.lease is self:self.session.lease=None
        self.released=True


class Session:
    def __init__(self,python,environment,config_path):
        from ..solver.runtime_guard import before_launch
        before_launch(environment)
        self.process=None;self.root=None;self.lease=None;self.poisoned=False;self.generation=0
        self.python=str(Path(python).resolve());self.environment=dict(environment)
        self.created=self.used=time.monotonic();self.token=uuid.uuid4().hex
        config=json.loads(Path(config_path).read_text(encoding='utf8'))
        self.variant=preload_config.variant_key(config);self.code=protocol.code_id()
        self.temp_root=Path(self.environment.get('TEMP') or tempfile.gettempdir()).resolve()
        self.root=Path(tempfile.mkdtemp(prefix='ac9_live_session_', dir=self.temp_root))
        try:
            protocol.write(self.root/'owner.json',dict(root=str(self.root.resolve()),token=self.token))
            protocol.write(self.root/'warm.json',preload_config.snapshot(config))
            protocol.write(self.root/'session.json',dict(version=1,session_token=self.token,code=self.code,
                environment=protocol.environment_id(environment),root=str(self.root.resolve())))
            worker=Path(protocol.__file__).with_name('live_session_worker.py')
            # stderr kept in the session folder (was DEVNULL): what Warp's C code or a fatal error writes between jobs;
            # a job routes fd 1/2 into its own stdout.txt (live_session_worker.route_fds)
            with open(self.root/'worker_stderr.txt','wb') as err:
                self.process=OwnedProcess([self.python,str(worker),str(self.root/'session.json')],
                    stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=err,text=True,
                    env=self.environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except BaseException:
            self.cleanup();raise
    def stderr_tail(self,size=4096):
        try:
            with open(self.root/'worker_stderr.txt','rb') as f:
                f.seek(0,2);n=f.tell();f.seek(max(0,n-size))
                return f.read().decode('utf8','replace')
        except (OSError,TypeError):return None
    def compatible(self,python,environment,config_path):
        return (not self.poisoned and self.process.poll() is None
                and protocol.same_interpreter(python,self.python) and self.environment==dict(environment)
                and self.code==protocol.code_id()
                and self.variant==preload_config.variant_key(json.loads(Path(config_path).read_text(encoding='utf8'))))
    def status(self):
        row=read(self.root/'status.json')
        return row if row and row.get('session_token')==self.token and row.get('version')==1 else None
    def submit(self,idx,avatar,config_path,stage,op='run'):
        """op 'build': fill the kept model only (the kept Live model, Fast Simulation Start), no frame and no result (resident_runner)."""
        from ..solver.runtime_guard import before_launch
        before_launch(self.environment)
        if op not in ('run','build'):raise ValueError('Unknown Native session job')
        if self.lease is not None:raise ValueError('Previous frontend lease has not been released')
        if not self.compatible(self.python,self.environment,config_path):raise ValueError('Incompatible Native session')
        cfg=json.loads(Path(config_path).read_text(encoding='utf8'))
        identity=protocol.fingerprint(idx,avatar,cfg,protocol.environment_id(self.environment),implementation=self.code,
                                      unaudited=protocol.SESSION_UNAUDITED)
        self.generation+=1
        command=dict(op=op,session_token=self.token,job_token=uuid.uuid4().hex,generation=self.generation,
                     args=list(map(str,(idx,avatar,config_path,stage))),fingerprint=identity,code=self.code)
        job=Job(self,command)
        protocol.write(self.root/(command['job_token']+'.request.json'),command)
        self.lease=job;self.used=time.monotonic()
        # Assign the owner before writing. An uncertain write can never be retried.
        try:
            header={key:command[key] for key in ('op','session_token','job_token','generation')}
            job.sent=True;self.process.stdin.write(json.dumps(header)+'\n');self.process.stdin.flush()
        except (OSError,ValueError) as exc:
            job.error=str(exc)
            self.poisoned=True
            self.close()
            # The caller receives the owned uncertain job, never permission to
            # launch another interpreter for the same request.
        return job
    def close(self):
        self.poisoned=True
        if self.lease is not None and self.lease.poll() is None:
            try:Path(self.lease.command['args'][3],'stop').touch()
            except OSError:pass
        if self.process is not None and self.process.stdin is not None:
            try:self.process.stdin.close()
            except OSError:pass
    def finish_without_reuse(self):
        """Keep Ready OFF never stops the running job; EOF is read afterwards."""
        self.poisoned=True
        if self.process is not None and self.process.stdin is not None:
            try:self.process.stdin.close()
            except OSError:pass
    def cleanup(self):
        if self.process is not None and self.process.poll() is None:return False
        if self.root is None or not self.root.exists():return True
        root=self.root.resolve()
        if (root.parent!=getattr(self,'temp_root',Path(tempfile.gettempdir()).resolve()) or not root.name.startswith('ac9_live_session_')
                or self.root.is_symlink() or self.root.is_junction()):raise ValueError('Unexpected Native session root')
        if not (root/'owner.json').exists():root.rmdir();return True
        owner=read(root/'owner.json')
        if owner!=dict(root=str(root),token=self.token):raise ValueError('Native session owner mismatch')
        shutil.rmtree(root);return True
