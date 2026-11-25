# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep imports/device alive, release every job model before accepting another."""
import faulthandler
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
from runtime_guard import verify_actual_environment
verify_actual_environment()
import prepared_live as protocol
import preload_config
from lifecycle_trace import mark
import job_cancel
from job_cancel import CanceledJob
# Resident Live (part of Preferences > Fast Simulation Start; DECISIONS cloth-sim-hstar-resident-2026-11-16):
# the worker keeps the last job's model and begins the next job of the same structure on it (resident_runner.py)
RESIDENT_ON = os.environ.get('AC9_RESIDENT') == '1'
RUNNER = None


def load_request(line,spec,root,generation):
    """Only a small ASCII header enters the pipe; long paths live in an atomic file."""
    if len(line)>4096:raise ValueError('Oversized Native request header')
    header=json.loads(line)
    if not isinstance(header,dict) or set(header)!=set(('op','session_token','job_token','generation')):
        raise ValueError('Invalid Native request header')
    token=header.get('job_token','')
    if (header.get('op') not in ('run','build') or header.get('session_token')!=spec['session_token']
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


_NEWTON_SEEN={}   # newton folder -> (the (path, mtime_ns, size) of every .py, the digest of their contents)


def _newton_digest(root):
    """The sha256 of newton's sources. Every .py is read once per process; a later job only compares each file's
    modified time and size with the ones the digest was made from (reading them all again took about 0.1 s on
    every job) and reads them again when any differs, a file is added or a file is gone."""
    stats=[]
    pending=[str(root)];cut=len(str(root))+1
    while pending:                 # os.scandir: on Windows the listing already holds each file's time and size
        job_cancel.check()
        with os.scandir(pending.pop()) as listing:
            for entry in listing:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif entry.name.endswith('.py'):
                    st=entry.stat()
                    stats.append((entry.path[cut:].replace(os.sep,'/'),st.st_mtime_ns,st.st_size))
    stats.sort()
    paths=[root/rel for rel,_,_ in stats]
    seen=_NEWTON_SEEN.get(str(root))
    if seen is not None and seen[0]==stats:
        return seen[1]
    digest=hashlib.sha256()
    for index, path in enumerate(paths):
        if index % 32 == 0:job_cancel.check()
        digest.update(path.relative_to(root).as_posix().encode());digest.update(path.read_bytes())
    _NEWTON_SEEN[str(root)]=(stats,digest.hexdigest())
    return _NEWTON_SEEN[str(root)][1]


def engine_identity(*,expected_code=None):
    mark('engine_identity_begin')
    import newton
    hexdigest=_newton_digest(Path(newton.__file__).absolute().parent)
    mark('engine_newton_hashed')
    identity=protocol.source_id(expected_code=expected_code)+hexdigest
    mark('engine_identity_complete')
    return identity


def warp_versions(stream):
    """Warp's start line ('CUDA Toolkit 12.9, Driver 12.2') in this job's stdout.txt. Warp prints it once, when this
    worker started (before any job), so a kernel build that failed in a later job left no versions in the job's output
    and core.gpu_failure called an old driver the add-on's fault instead of asking for a driver update (release check,
    2026-11-23). Nothing when Warp is not loaded yet: it then prints the line itself, into this file."""
    wp=sys.modules.get('warp')
    if wp is None:
        return
    try:
        from warp._src import context
        rt=context.runtime
    except Exception:
        rt=getattr(getattr(wp,'context',None),'runtime',None)
    try:  # a diagnostic line must never fail the job
        tk=getattr(rt,'toolkit_version',None);dv=getattr(rt,'driver_version',None)
        if tk:
            stream.write('CUDA Toolkit %d.%d, %s\n'%(tk[0],tk[1],'Driver %d.%d'%tuple(dv[:2]) if dv else 'CUDA driver not available'))
    except Exception:
        pass


def run_job(command,spec,variant,engine):
    """No model, callback, exception traceback or graph escapes this boundary."""
    stage=Path(command['args'][3]);closes=[];result=None
    argv=sys.argv;out,err=sys.stdout,sys.stderr;paths=list(sys.path);environment=dict(os.environ)
    code=0;message=None;stream=None;audit={};job_globals=[];profile=None;fds=None
    import lifecycle_trace
    original_trace=lifecycle_trace.mark
    def trace(event,**fields):
        if event=='solver_import_begin' and not job_globals and not RESIDENT_ON:
            job_globals.append(sys._getframe(1).f_globals)
        if event=='loop_ready' and os.environ.get('AC9_SESSION_AUDIT')=='1':
            g=sys._getframe(1).f_globals
            audit['initial']=protocol.initial_hashes(g)
            references={}
            for name in ('model','solver','s0','s1','graph'):
                try:references[name]=weakref.ref(g[name])
                except TypeError:pass
            audit['_references']=references
        elif event in ('timing_built','timing_finalized','timing_filters','timing_solver','start_check_begin') and os.environ.get('AC9_SESSION_AUDIT')=='1':
            g=sys._getframe(1).f_globals
            references=audit.setdefault('_references',{})
            for name in ('builder','model','solver','s0','s1','graph'):
                try:references[name]=weakref.ref(g[name])
                except (KeyError,TypeError):pass
        return original_trace(event,**fields)
    try:
        job_cancel.activate(stage,command['job_token'],command['generation'])
        job_cancel.check()
        if command['session_token']!=spec['session_token'] or command['code']!=spec['code']:
            raise ValueError('Stale Native session request')
        if engine_identity(expected_code=spec['code'])!=engine:raise ValueError('Native session source/dependency changed')
        idx,avatar,cfg_path,_=command['args']
        cfg=json.loads(Path(cfg_path).read_text(encoding='utf8'))
        if preload_config.variant_key(cfg)!=variant:raise ValueError('Native compile variant changed')
        mark('session_fingerprint_begin')
        if protocol.fingerprint(idx,avatar,cfg,spec['environment'],implementation=spec['code'],
                                unaudited=protocol.SESSION_UNAUDITED)!=command['fingerprint']:
            raise ValueError('Native request input changed')
        mark('session_fingerprint_complete')
        build=command['op']=='build'
        if build and (not RESIDENT_ON or any(k in cfg for k in ('native_runtime_dir','native_runtime_token'))):
            # a build only fills the kept model (the kept Live model, Fast Simulation Start); it never owns Live output (no runtime)
            audit['build']='refused';return code,message,audit
        if any((stage/name).exists() for name in ('run_log.json','frames.npy','live.npy')):
            raise ValueError('Native session refuses an existing result')
        job_cancel.check()
        # line buffered, and the process's own fd 1/2 (where Warp's C code and a fatal error write) into the same file:
        # the session's are DEVNULL, so a native failure left no trace (2026-11-21, a heap corruption)
        stream=(stage/'stdout.txt').open('w',encoding='utf8',buffering=1)
        sys.stdout=sys.stderr=stream
        fds=route_fds(stream)
        warp_versions(stream)
        sys.argv=[str(Path(__file__).with_name('newton_vbd.py')),idx,avatar,cfg_path,str(stage)]
        lifecycle_trace.mark=trace
        if os.environ.get('AC9_SESSION_PROFILE')=='1':
            import cProfile
            profile=cProfile.Profile();profile.enable()
        init={'_AC9_CLEANUP':closes.append,'_AC9_CANCEL':job_cancel.check,'_AC9_LOOP_READY':job_cancel.loop_ready,'_AC9_BUSY_BEGIN':job_cancel.busy_begin,'_AC9_BUSY_END':job_cancel.busy_end}
        if RESIDENT_ON:
            # the kept model is the runner's, not this job's: it is not cleared below (job_globals stays empty)
            global RUNNER
            import resident_runner
            if RUNNER is None:RUNNER=resident_runner.Resident(sys.argv[0],Path(__file__).parent)
            try:audit['resident']=RUNNER.run(idx,avatar,cfg,init,build_only=build)
            finally:
                last=RUNNER.last or {}
                mark('resident_job',mode=last.get('mode'),why_fresh=str(last.get('why_fresh'))[:400],
                     why_not=str(last.get('why_not'))[:300],begin_error=last.get('begin_error'),vram=last.get('vram'))
        else:
            result=runpy.run_path(sys.argv[0],run_name='__main__',init_globals=init)
        if not build:
            log=json.loads((stage/'run_log.json').read_text(encoding='utf8'))
            if log.get('error'):code=2;message=str(log['error'])
    except CanceledJob:
        audit['cancelled']=True
        # Cancellation before frame 1 is a completed request, not a failed I/O
        # runtime. Close its streams/locks through the usual owned cleanup path.
        namespaces=list(job_globals)
        if RUNNER is not None:namespaces.append(RUNNER.g if RUNNER.g is not None else {'_NATIVE_RUNTIME':RUNNER.runtime})
        for namespace in namespaces:
            runtime=namespace.get('_NATIVE_RUNTIME')
            if runtime is not None:runtime.success=True
    except BaseException as exc:
        code=1;message=str(exc)
        try:traceback.print_exc()
        except Exception:pass
    finally:
        job_cancel.clear()
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
        restore_fds(fds)
        sys.argv=argv;sys.stdout,sys.stderr=out,err;sys.path[:]=paths
        os.environ.clear();os.environ.update(environment)
        if stream is not None:
            try:stream.close()
            except BaseException as exc:code=1;message='Job output cleanup: '+str(exc)
    return code,message,audit


def route_fds(stream):
    """fd 1 and 2 into `stream`'s file and faulthandler onto it; returns what restore_fds needs (None: not done)."""
    try:
        for f in (sys.__stdout__, sys.__stderr__):
            if f is not None:f.flush()
        saved=(os.dup(1),os.dup(2))
        os.dup2(stream.fileno(),1);os.dup2(stream.fileno(),2)
    except (OSError,ValueError,AttributeError):
        return None
    try:faulthandler.enable(file=stream,all_threads=True)
    except (OSError,ValueError,RuntimeError):pass
    return saved


def restore_fds(saved):
    if saved is None:return
    try:faulthandler.enable(file=sys.__stderr__,all_threads=True)     # back onto the session's worker_stderr.txt
    except (OSError,ValueError,RuntimeError,AttributeError):pass
    for fd,old in zip((1,2),saved):
        try:os.dup2(old,fd);os.close(old)
        except OSError:pass


def main(path):
    try:faulthandler.enable(file=sys.__stderr__,all_threads=True)    # the session's worker_stderr.txt
    except (OSError,ValueError,RuntimeError,AttributeError):pass
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
        engine=engine_identity(expected_code=spec['code']);status('idle')
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
