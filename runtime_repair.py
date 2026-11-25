# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned asynchronous staging; bounded publication on Blender's main thread."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from .solver.runtime_guard import pending_journals


class SessionCookie:
    """Windows removes this non-inherited file handle even after os._exit or process termination."""
    def __init__(self, root):
        if os.name!='nt':raise RuntimeError('Runtime publication requires Windows session ownership')
        self.path=Path(root)/'session.cookie'
        self.fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_TEMPORARY,0o600)
        if not self.path.exists():self.close();raise RuntimeError('Cannot create runtime session ownership')
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None


class RepairJob:
    def __init__(self, snapshot, guard, lock_factory, *, check_only=False, cleanup_roots=()):
        from .solver.process_tree import OwnedProcess
        self.factory = OwnedProcess
        self.snapshot, self.guard, self.lock_factory = snapshot, guard, lock_factory
        self.cleanup_roots=list(cleanup_roots)
        self.token = uuid.uuid4().hex
        self.parent = Path(snapshot['extensions']).resolve()/'.ac9_runtime_repair'
        self.parent.mkdir(exist_ok=True)
        if self.parent.resolve().parent != Path(snapshot['extensions']).resolve():
            raise ValueError('Unexpected repair directory')
        self.root = self.parent/self.token
        self.root.mkdir()
        (self.root/'owner.json').write_text(json.dumps({'token': self.token}), encoding='utf8')
        self.cookie=SessionCookie(self.root)
        self.site = Path(snapshot['site']).resolve()
        self.process = None
        self.state, self.message = ('validating' if check_only else 'staging'), ''
        self.published=False
        self.cancelled = False
        self.created = time.monotonic()
        self.publish_seconds = None
        self.publish_timings = {}
        self.validation = None
        self._exit_seen = None
        try:self._start('check' if check_only else 'stage')
        except BaseException:
            self.cookie.close();self.cleanup();raise

    @property
    def busy(self):
        return self.state in {'staging', 'validating', 'cancelling'}

    @property
    def cancellable(self):return self.state=='staging' and not self.published

    def _start(self, mode):
        self._exit_seen = None
        self.spec = dict(self.snapshot, token=self.token, mode=mode,cleanup_roots=self.cleanup_roots)
        path = self.root/'request.json'
        path.write_text(json.dumps(self.spec), encoding='utf8')
        receipt = self.root/'receipt.json'
        if receipt.exists():
            receipt.unlink()
        with (self.root/'worker.log').open('ab') as log:
            self.process = self.factory([sys.executable, '-B', str(Path(__file__).with_name('runtime_repair_worker.py')), str(path)],
                stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, CUDA_VISIBLE_DEVICES='-1'),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def cancel(self):
        if self.cancellable:
            self.cancelled = True
            self.state = 'cancelling'
            if self.process is not None:
                self.process.kill()
            return True
        return False

    def _journal(self,state,identities=None):
        path=self.root/'publication.json'
        row=json.loads(path.read_text(encoding='utf8')) if path.exists() else {
            'token':self.token,'site':str(self.site),'identities':identities}
        row['state']=state
        temp=self.root/'publication.tmp';temp.write_text(json.dumps(row),encoding='utf8');temp.replace(path)

    def process_poll(self, process=None, marker='_exit_seen'):
        """Allow Windows job accounting to settle; never accept a live descendant."""
        process=self.process if process is None else process
        if process is None or process.process.poll() is None:
            return None
        if process.job is not None and process.job.active():
            if getattr(self,marker,None) is None:
                setattr(self,marker,time.monotonic())
            if time.monotonic()-getattr(self,marker) < 1.0:
                return None
            process.abnormal = True
            process.kill()
            return None
        return process.poll()

    def _publish(self, row):
        stage = Path(row['stage_site']).resolve()
        expected = self.root/'stage'/'.local'/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
        if stage != expected or stage.is_symlink() or stage.is_junction():
            raise RuntimeError('Unexpected repair staging path')
        entries = sorted(stage.iterdir(), key=lambda p:p.name)
        if [p.name for p in entries] != row['entries']:
            raise RuntimeError('The staged runtime changed')
        start = time.perf_counter()
        # Repo locks exclude standard package updates. Enable/disable callbacks run
        # on this same main thread and cannot interleave the bounded rename batch.
        with self.lock_factory(self.root) as locks:
            self.publish_timings['locks_seconds'] = time.perf_counter()-start
            self.lock_errors = {directory:error for directory,error in locks.items() if error is not None}
            if any(error is not None for error in locks.values()):
                raise RuntimeError('Another extension operation is in progress')
            part = time.perf_counter()
            self.guard(self.snapshot)
            self.publish_timings['guard_seconds'] = time.perf_counter()-part
            self.site.mkdir(parents=True, exist_ok=True)
            if any((self.site/p.name).exists() or (self.site/p.name).is_symlink() for p in entries):
                raise RuntimeError('Runtime files changed while repair was preparing')
            identities = {p.name:(p.stat().st_dev,p.stat().st_ino) for p in entries}
            if any(not identity[1] for identity in identities.values()):
                raise RuntimeError('Cannot verify temporary runtime files')
            self._journal('publishing',identities)
            self.published=True
            moved = []
            try:
                for source in entries:
                    part = time.perf_counter()
                    target = self.site/source.name
                    source.rename(target)
                    moved.append((source,target))
                    self.publish_timings[source.name] = time.perf_counter()-part
            except Exception:
                for source,target in reversed(moved):
                    stat = target.stat()
                    if (stat.st_dev,stat.st_ino) != identities[source.name] or source.exists():
                        raise RuntimeError('Runtime publication could not be safely rolled back')
                    target.rename(source)
                self._journal('rolled_back')
                self.published=False
                raise
            self._journal('published')
        self.publish_seconds = time.perf_counter()-start
        if str(self.site) not in sys.path:
            sys.path.append(str(self.site))
        importlib.invalidate_caches()

    def poll(self):
        if not self.busy or self.process is None:
            return self.state
        code = self.process_poll()
        if code is None:
            return self.state
        self.terminal = {'wrapped_code':code,'raw_code':self.process.process.returncode,
                         'abnormal':self.process.abnormal,'process_error':self.process.error}
        if self.cancelled:
            self.state = 'cancelled'
            return self.state
        try:
            row = json.loads((self.root/'receipt.json').read_text(encoding='utf8'))
            if code or not row.get('ok') or row.get('token') != self.token:
                raise RuntimeError(row.get('message', 'Runtime repair failed'))
            if self.state == 'staging':
                self._publish(row)
                self.state = 'validating'
                self._start('check')
            else:
                self.guard(self.snapshot)
                self.validation = row['validation']
                self.verified_entries=set(row['verified_entries'])
                if self.published:self._journal('verified')
                self.state = 'ready'
        except Exception as error:
            self.message = str(error)
            self.state = 'failed'
        return self.state

    def cleanup(self):
        if self.process is not None and self.process_poll() is None:
            return False
        if hasattr(self, '_cleanup_process'):
            code = self.process_poll(self._cleanup_process,'_cleanup_exit_seen')
            if code is None:return False
            if code or self.root.exists():
                status=self.root/'cleanup_error.json'
                self.cleanup_error=(json.loads(status.read_text(encoding='utf8')) if status.exists()
                                    else {'message':'Owned runtime cleanup did not finish','code':code})
            return True
        if not self.root.exists():
            if hasattr(self,'cookie'):self.cookie.close()
            return True
        if hasattr(self,'cookie'):self.cookie.close()
        if getattr(self,'published',False) and self.state!='ready':
            self.cleanup_retained=True
            return True  # Terminal retention; the publication guard still blocks launch.
        # Only this job's verified UUID directory can be recursively removed.
        if (self.root.is_symlink() or self.root.is_junction() or self.root.resolve().parent != self.parent.resolve()
                or self.root.name != self.token
                or json.loads((self.root/'owner.json').read_text(encoding='utf8')) != {'token':self.token}):
            raise ValueError('Unexpected runtime repair owner')
        # Recursive deletion may be slow or briefly denied after terminating a
        # Windows process. Never run it in Blender's main-thread timer.
        if not hasattr(self, '_cleanup_process'):
            from .solver.process_tree import OwnedProcess
            request = self.root/'cleanup.json'
            request.write_text(json.dumps({'token':self.token,'mode':'cleanup',
                'extensions':str(self.parent.parent)}), encoding='utf8')
            self._cleanup_process = OwnedProcess([sys.executable, '-B',
                str(Path(__file__).with_name('runtime_repair_worker.py')),str(request)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=dict(os.environ,CUDA_VISIBLE_DEVICES='-1'),
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return False
