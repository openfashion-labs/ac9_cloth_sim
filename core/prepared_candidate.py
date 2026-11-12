# SPDX-License-Identifier: GPL-3.0-or-later
"""An owned, disposable candidate. Fresh click-time preparation always wins."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid
import numpy as np
from ..solver import prepared_live as protocol
from ..solver.process_tree import OwnedProcess


class Candidate:
    def __init__(self, python, environment, idx, avatar, config_path, generation, *, positions=None):
        self.process = None
        self.root = None
        try:
            self._initialize(python, environment, idx, avatar, config_path, generation, positions)
        except BaseException:
            self.cleanup()
            raise

    def _initialize(self, python, environment, idx, avatar, config_path, generation, positions):
        self.python = str(Path(python).resolve())
        self.environment = dict(environment)
        self.generation = generation
        self.token = uuid.uuid4().hex
        self.created = time.monotonic()
        self.consumed = False
        self.process = None
        self.root = Path(tempfile.mkdtemp(prefix='ac9_prepared_candidate_'))
        protocol.write(self.root/'owner.json',dict(root=str(self.root.resolve()),token=self.token))
        self.status_path = self.root / 'status.json'
        stage = self.root / 'private'; stage.mkdir()
        config = json.loads(Path(config_path).read_text(encoding='utf8'))
        # Validate before spawning. The child repeats validation on its own files.
        protocol.fingerprint(idx, avatar, config, protocol.environment_id(environment), implementation='eligibility')
        for key in ('native_runtime_dir', 'native_runtime_token'):
            config.pop(key, None)
        config.update(grab_file=str(stage / 'grab.json'), params_file=str(stage / 'params.json'))
        def copy_input(value, name):
            if isinstance(value, dict):return {key:copy_input(child,name+'_'+key) for key,child in value.items()}
            if isinstance(value, list):return [copy_input(child,name+'_'+str(i)) for i,child in enumerate(value)]
            if isinstance(value,str) and Path(value).is_file():
                target=self.root/(name+Path(value).suffix);shutil.copy2(value,target);return str(target)
            return value
        config = {key:copy_input(value,'config_'+key) for key,value in config.items()}
        idx_copy=self.root/'scene.idx.npz';avatar_copy=self.root/'avatar.npz'
        shutil.copy2(idx,idx_copy);shutil.copy2(avatar,avatar_copy)
        if positions is not None:
            with np.load(idx_copy,allow_pickle=False) as archive:arrays={key:archive[key] for key in archive.files}
            if 'Xclo' not in arrays or not np.array_equal(arrays['Xclo'],arrays['Xinit']):
                raise ValueError('Prepared forecast requires the standard equal starting-position arrays')
            value=np.asarray(positions,dtype=arrays['Xinit'].dtype)
            if value.shape!=arrays['Xinit'].shape or not np.isfinite(value).all():
                raise ValueError('Invalid prepared snapshot positions')
            arrays['Xinit']=value.copy();arrays['Xclo']=value.copy();np.savez(idx_copy,**arrays)
        cfg=stage/'config.json';protocol.write(cfg,config)
        spec=dict(token=self.token,generation=generation,status=str(self.status_path),idx=str(idx_copy),
                  avatar=str(avatar_copy),config=config,config_path=str(cfg),stage=str(stage))
        specification=self.root/'request.json';protocol.write(specification,spec)
        self.input_identity=protocol.fingerprint(idx_copy,avatar_copy,config,protocol.environment_id(environment),implementation='pending-input')
        self.code_identity=protocol.code_id()
        worker=Path(protocol.__file__).with_name('prepared_worker.py')
        self.process=OwnedProcess([self.python,str(worker),str(specification)],stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True,env=self.environment,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def status(self):
        if getattr(self.process,'error',None):
            return dict(version=protocol.VERSION,token=self.token,generation=self.generation,phase='failed',reason=self.process.error)
        try:
            if self.status_path.stat().st_size>1048576:return None
            value=json.loads(self.status_path.read_text(encoding='utf8'))
            if not isinstance(value,dict) or value.get('version')!=protocol.VERSION:return None
            if value.get('token')!=self.token or value.get('generation')!=self.generation:return None
            return value
        except (OSError,ValueError,TypeError):return None

    def matches(self, python, environment, idx, avatar, config_path, generation, *, allow_loading=False):
        if self.consumed or generation!=self.generation or self.process.poll()is not None:return False
        if str(Path(python).resolve())!=self.python or dict(environment)!=self.environment:return False
        ready=self.status()
        if ready is None and allow_loading:
            # Before the worker publishes its manifest, retain only a numeric-
            # input match. No start can be sent until ready performs engine and
            # full source validation in PendingStart.poll().
            try:
                config=json.loads(Path(config_path).read_text(encoding='utf8'))
                return (self.code_identity==protocol.code_id() and
                        self.input_identity==protocol.fingerprint(idx,avatar,config,protocol.environment_id(environment),implementation='pending-input'))
            except (OSError,ValueError,KeyError,TypeError):return False
        phases=('prepared','loading') if allow_loading else ('prepared',)
        if not ready or ready.get('phase')not in phases:return False
        try:
            implementation=protocol.source_id(ready['dependencies'])
            if implementation!=ready['implementation']:return False
            config=json.loads(Path(config_path).read_text(encoding='utf8'))
            identity=protocol.fingerprint(idx,avatar,config,protocol.environment_id(environment),implementation=implementation)
            return identity==ready['fingerprint']
        except (OSError,ValueError,KeyError,TypeError,AttributeError):return False

    def start(self, idx, avatar, config_path, stage):
        if self.consumed:raise ValueError('Prepared candidate was already consumed')
        self.consumed=True
        ready=self.status()
        command=dict(op='start',token=self.token,generation=self.generation,
                     args=[str(idx),str(avatar),str(config_path),str(stage)],fingerprint=ready['fingerprint'])
        # Once attempted, an uncertain write is never resent or replaced concurrently.
        self.process.stdin.write(json.dumps(command)+'\n');self.process.stdin.flush()
        return self.process

    def cancel(self):
        self.consumed=True
        if self.process is not None and self.process.stdin is not None:
            try:self.process.stdin.close()
            except OSError:pass

    def cleanup(self):
        if self.process is not None and self.process.poll() is None:return False
        if self.root is None or not self.root.exists():return True
        root=self.root.resolve()
        if (root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('ac9_prepared_candidate_')
                or self.root.is_symlink() or self.root.is_junction()):
            raise ValueError('Unexpected prepared candidate cleanup root')
        if not (root/'owner.json').exists():
            root.rmdir()  # Only a newly created, still-empty constructor root.
            return True
        owner=json.loads((root/'owner.json').read_text(encoding='utf8'))
        if owner!=dict(root=str(root),token=self.token):raise ValueError('Prepared candidate cleanup owner changed')
        shutil.rmtree(root)
        return True


class Slot:
    def __init__(self):
        self.generation=0
        self.candidate=None

    def clear(self):
        self.generation+=1
        old=self.candidate;self.candidate=None
        if old is not None:old.cancel()
        return old

    def take(self,python,environment,idx,avatar,config_path,*,allow_loading=False):
        candidate=self.candidate
        if candidate is None:return None
        if not candidate.matches(python,environment,idx,avatar,config_path,self.generation,allow_loading=allow_loading):return None
        self.candidate=None
        return candidate


class PendingStart:
    """Own a matching build without sending a request before it is ready."""
    def __init__(self,candidate,python,environment,idx,avatar,config_path,stage,generation):
        self.candidate=candidate
        self.inputs=(python,dict(environment),idx,avatar,config_path,generation)
        self.args=(idx,avatar,config_path,stage)
        self.state='waiting'
        self.reason=''

    def poll(self):
        if self.state!='waiting':return self.state
        candidate=self.candidate
        if candidate.consumed:
            self.state='canceled';return self.state
        if candidate.process.poll()is not None:
            self.state='rejected';self.reason='Prepared worker exited';return self.state
        row=candidate.status()
        if row is None or row.get('phase')=='loading':return self.state
        if row.get('phase')!='prepared' or not candidate.matches(*self.inputs):
            self.state='rejected';self.reason='Prepared worker or inputs changed';return self.state
        # An attempted write is never retried, even if the pipe reports failure.
        self.state='uncertain'
        try:candidate.start(*self.args)
        except (OSError,ValueError,TypeError,KeyError)as exc:
            self.reason=str(exc);candidate.cancel()
        else:self.state='started'
        return self.state

    def cancel(self):
        if self.state!='started':self.state='canceled'
        self.candidate.cancel()
