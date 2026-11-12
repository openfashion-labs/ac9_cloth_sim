# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep imports/device alive, release every job model before accepting another."""
import gc
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import traceback
import weakref
from process_tree import join_owned_job
join_owned_job()
import prepared_live as protocol
import preload_config
from lifecycle_trace import mark

class CanceledJob(Exception):pass


def load_request(line,spec,root,generation):
    """Only a small ASCII header enters the pipe; long paths live in an atomic file."""
    if len(line)>4096:raise ValueError('Oversized Native request header')
    header=json.loads(line)
    if not isinstance(header,dict) or set(header)!=set(('op','session_token','job_token','generation')):
        raise ValueError('Invalid Native request header')
    token=header.get('job_token','')
    if (header.get('op')!='run' or header.get('session_token')!=spec['session_token']
            or not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef'for c in token)
            or type(header.get('generation'))is not int or header['generation']<=generation):
        raise ValueError('Invalid Native job generation')
    path=root/(token+'.request.json')
    if path.stat().st_size>65536:raise ValueError('Oversized Native request file')
    command=json.loads(path.read_text(encoding='utf8'))
    if (not isinstance(command,dict) or type(command.get('generation'))is not int or any(command.get(k)!=header[k]for k in header)
            or not isinstance(command.get('args'),list) or len(command['args'])!=4
            or not all(isinstance(value,str)for value in command['args'])):
        raise ValueError('Native request header mismatch')
    return command


def engine_identity():
    import newton
    digest=hashlib.sha256()
    root=Path(newton.__file__).resolve().parent
    for path in sorted(root.rglob('*.py')):
        digest.update(path.relative_to(root).as_posix().encode());digest.update(path.read_bytes())
    return protocol.source_id()+digest.hexdigest()


def run_job(command,spec,variant,engine):
    """No model, callback, exception traceback or graph escapes this boundary."""
    stage=Path(command['args'][3]);closes=[];result=None
    argv=sys.argv;out,err=sys.stdout,sys.stderr;paths=list(sys.path);environment=dict(os.environ)
    code=0;message=None;stream=None;audit={};job_globals=[];profile=None
    import lifecycle_trace
    original_trace=lifecycle_trace.mark
    def trace(event,**fields):
        if event=='solver_import_begin' and not job_globals:
            job_globals.append(sys._getframe(1).f_globals)
        if event=='loop_ready' and os.environ.get('AC9_SESSION_AUDIT')=='1':
            g=sys._getframe(1).f_globals
            audit['initial']=protocol.initial_hashes(g)
            references={}
            for name in ('model','solver','s0','s1','graph'):
                try:references[name]=weakref.ref(g[name])
                except TypeError:pass
            audit['_references']=references
        return original_trace(event,**fields)
    try:
        if command['session_token']!=spec['session_token'] or command['code']!=spec['code']:
            raise ValueError('Stale Native session request')
        if engine_identity()!=engine:raise ValueError('Native session source/dependency changed')
        idx,avatar,cfg_path,_=command['args']
        cfg=json.loads(Path(cfg_path).read_text(encoding='utf8'))
        if preload_config.variant_key(cfg)!=variant:raise ValueError('Native compile variant changed')
        if protocol.fingerprint(idx,avatar,cfg,spec['environment'],implementation=spec['code'])!=command['fingerprint']:
            raise ValueError('Native request input changed')
        if any((stage/name).exists() for name in ('run_log.json','frames.npy','live.npy')):
            raise ValueError('Native session refuses an existing result')
        if (stage/'stop').exists():raise CanceledJob()
        stream=(stage/'stdout.txt').open('w',encoding='utf8')
        sys.stdout=sys.stderr=stream
        sys.argv=[str(Path(__file__).with_name('newton_vbd.py')),idx,avatar,cfg_path,str(stage)]
        lifecycle_trace.mark=trace
        if os.environ.get('AC9_SESSION_PROFILE')=='1':
            import cProfile
            profile=cProfile.Profile();profile.enable()
        result=runpy.run_path(sys.argv[0],run_name='__main__',init_globals={'_AC9_CLEANUP':closes.append})
        log=json.loads((stage/'run_log.json').read_text(encoding='utf8'))
        if log.get('error'):code=2;message=str(log['error'])
    except CanceledJob:
        pass
    except BaseException as exc:
        code=1;message=str(exc)
        try:traceback.print_exc()
        except Exception:pass
    finally:
        if profile is not None:
            profile.disable()
            import pstats
            stats=pstats.Stats(profile)
            audit['profile']=[dict(file=k[0],line=k[1],name=k[2],calls=v[1],self_s=v[2],total_s=v[3])
                for k,v in sorted(stats.stats.items(),key=lambda pair:pair[1][3],reverse=True)[:35]]
        try:
            import warp as wp
            wp.synchronize()
        except BaseException as exc:code=1;message='GPU synchronization: '+str(exc)
        for close in reversed(closes):
            try:close()
            except BaseException as exc:code=1;message='Job cleanup: '+str(exc)
        closes.clear()
        # Destroy the actual run globals, not just runpy's returned copy. Kernel
        # functions may still be registered; their globals must not retain models.
        for namespace in job_globals:namespace.clear()
        job_globals.clear()
        if result is not None:result.clear()
        lifecycle_trace.mark=original_trace
        sys.argv=argv;sys.stdout,sys.stderr=out,err;sys.path[:]=paths
        os.environ.clear();os.environ.update(environment)
        if stream is not None:
            try:stream.close()
            except BaseException as exc:code=1;message='Job output cleanup: '+str(exc)
    return code,message,audit


def main(path):
    spec=json.loads(Path(path).read_text(encoding='utf8'));root=Path(spec['root'])
    def status(phase,**fields):
        protocol.write(root/'status.json',dict(version=1,session_token=spec['session_token'],phase=phase,pid=os.getpid(),**fields))
    generation=0
    try:
        if protocol.code_id()!=spec['code']:raise ValueError('Native source changed before session startup')
        status('warming')
        argv=sys.argv;paths=list(sys.path)
        with (root/'startup.txt').open('w',encoding='utf8') as stream:
            out,err=sys.stdout,sys.stderr;sys.stdout=sys.stderr=stream
            try:
                sys.argv=[str(Path(__file__).with_name('newton_vbd.py')),'','',str(root/'warm.json')]
                try:runpy.run_path(sys.argv[0],run_name='__main__',init_globals={'_AC9_PREWARM':True})
                except SystemExit as exc:
                    if exc.code not in (None,0):raise
                import warp as wp
                wp.zeros(1,device=wp.get_preferred_device());wp.synchronize()
            finally:sys.argv=argv;sys.path[:]=paths;sys.stdout,sys.stderr=out,err
        variant=preload_config.variant_key(json.loads((root/'warm.json').read_text(encoding='utf8')))
        engine=engine_identity();status('idle')
        for line in sys.stdin:
            command=load_request(line,spec,root,generation);token=command['job_token']
            generation=command['generation'];status('running',generation=generation,job_token=token)
            mark('session_job_begin',generation=generation,job_token=token)
            code,message,audit=run_job(command,spec,variant,engine)
            # run_job has returned, so its last callback/traceback/model locals
            # are no longer roots. A failure here poisons the entire interpreter.
            try:gc.collect()
            except BaseException as exc:code=1;message='Reference cleanup: '+str(exc)
            references=audit.pop('_references',{})
            if references:audit['retained_after_cleanup']={name:ref() is not None for name,ref in references.items()}
            status('poisoned' if code else 'idle',generation=generation)
            protocol.write(root/(token+'.json'),dict(version=1,session_token=spec['session_token'],
                job_token=token,generation=generation,phase='complete',code=code,message=message,audit=audit))
            mark('session_job_complete',generation=generation,code=code)
            if code:return 1
        return 0
    except BaseException as exc:
        try:status('poisoned',message=str(exc))
        except Exception:pass
        return 1


if __name__=='__main__':os._exit(main(sys.argv[1]))
