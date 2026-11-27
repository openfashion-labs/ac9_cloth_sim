# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the next Simulate run's model ahead, while the drafting is still (part of Fast Simulation Start).

After a pattern edit the garment's structure changes, so the press found nothing kept and the waiting solver built
the whole model again (about 3-4 s on a T-shirt) before the first frame. Here, once the drafting has been still
for a moment, the job input a Live press would write is made in a temporary folder -- nothing in the scene is
written -- and the waiting solver is asked to build its model only ('build' job, solver/resident_runner.py). The
press stays an ordinary job: the solver begins on the built model when its structure key is the same, else it
builds from scratch as before. Design: REVIEW_fable_edit_time_begin_2026-11-21 (steps 3, promises 1-7).

Covered now: a Simulate run that goes on from a dressed garment's result (the common case after a pattern edit). A
garment with no result yet is not built ahead (its placed start writes the placed objects). Off by default for now
(each build holds the interface for about 0.4 s once the drafting is still): Preferences > Fast Simulation Start, or
AC9_BUILD_AHEAD=1."""
import json
import os
import time
from pathlib import Path

import bpy

QUIET_S = 0.5                     # still drafting for this long before a build is asked for (user 2026-11-22: 1.5 -> 0.5,
#                                   the press often comes 0.5-1.5 s after an edit; each build pauses the interface ~0.2 s)
STOP_GIVE_UP_S = 1.0              # a press waits this long for a build of another garment to stop, then restarts
FRESH_GUESS_S = 4.0               # a build's time before one has been measured
RUNTIME_KEYS = ('native_runtime_dir', 'native_runtime_token')
_A = dict(due=0.0, job=None, key=None, prep=None, failed=None, stop_t=None, press_key=None, last=None, wait_t=None,
          skip=None, generation=0)
_QUIET = {'on': False}             # dress.log: no scene writes while on


def switched_on():
    """Part of Preferences > Fast Simulation Start (on by default); AC9_BUILD_AHEAD=1 / 0 overrides it (development)."""
    env = os.environ.get('AC9_BUILD_AHEAD')
    if env in ('0', '1'):
        return env == '1'
    try:
        from . import dress as DR
        return DR.fast_start(bpy.context)
    except Exception:
        return False


def enabled(context=None):
    from . import dress as DR
    ctx = context or bpy.context
    return (switched_on() and not bpy.app.background
            and DR.resident_live(ctx) and DR._session_enabled(ctx))


def later(context=None, delay=QUIET_S):
    """A pattern or particle-distance edit: ask for a build once the drafting has been still for `delay` s. Later
    edits push it back; a build of the edit before is stopped (it is out of date)."""
    if not switched_on():
        return
    _A['generation'] += 1
    _A['due'] = time.monotonic() + delay
    _stop_running()
    if _A['job'] is None:_abort_prep()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=delay, persistent=False)


def owns(job):
    return job is not None and job is _A['job']


def _trace(event, **fields):
    try:
        from .solver.lifecycle_trace import mark
        mark(event, **fields)
    except Exception:
        pass


def _modal_running():
    """A modal operator in any window (G / R in the 3D view, a tool's drag, a number typed into a field while it
    is dragged): the build waits until it ends."""
    try:
        wm = bpy.context.window_manager
        return any(len(w.modal_operators) for w in wm.windows)
    except (AttributeError, TypeError):
        return False


def _skip(why, **fields):
    """Why no build was sent (once per reason in a row, so a wait does not fill the trace)."""
    if _A.get('skip') != why:
        _A['skip'] = why
        _trace('build_ahead_skip', why=why, **fields)


def _tick():
    wait = _A['due'] - time.monotonic()
    if wait > 0.02:
        return max(wait, 0.2)
    if _A['prep'] is not None and _A['job'] is None:return 0.1
    if _A['job'] is not None:                       # the one before is still stopping or ending
        _skip('previous_build')
        return 0.3
    try:
        if not enabled():
            _skip('not_enabled')
            return None
        if _busy_retry():
            # the mesh made on a background thread (dress.prewarm_build) ends on its own shortly: looked at again
            # soon, not in 0.3 s (the build waited up to 0.3 s after the mesh was made)
            from . import dress as DR
            return 0.05 if DR._prewarm_running() else 0.3
        _start(bpy.context)
    except Exception as exc:                        # an optional head start: the press builds as before
        _trace('build_ahead_error', error=repr(exc)[:300])
        _abort_prep()
    return None


def _busy_retry():
    from . import dress as DR, draft_tool as DT
    if str(getattr(bpy.context,'mode','')).startswith('EDIT'):
        _skip('editing_mesh');return True
    for why, now in (('mesh_prewarm', DR._prewarm_running), ('drag', lambda: DT._state.get('drag') is not None),
                     ('foreground', lambda: DR._SESSION_FOREGROUND), ('modal', _modal_running),
                     ('simulating', lambda: getattr(DR.AC9_OT_SimDress, '_active_instance', None) is not None)):
        if now():
            _skip(why)
            return True
    return False


def pressed():
    """A press's job was sent to the waiting solver: an edit's build still waiting for the drafting to be still
    is not wanted (the press builds or begins on its own; a build asked for after it found the model kept and did
    nothing, holding the interface 0.14-0.15 s each, three times in one test)."""
    _A['generation'] += 1
    _A['due'] = 0.0
    if _A['job'] is None:_abort_prep()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
        _skip('pressed')


def _input_update(scene, graph):
    task=_A['prep']
    if task is None or not hasattr(task,'dependencies'):return
    for update in graph.updates:
        item=getattr(update.id,'original',update.id)
        if item.session_uid in task.dependencies and (update.is_updated_geometry or update.is_updated_transform
                                                      or isinstance(item,bpy.types.Collection)):
            _trace('build_ahead_superseded',generation=_A['generation'],reason='scene_input')
            later(bpy.context);return


def _watch_inputs(on):
    handlers=bpy.app.handlers.depsgraph_update_post
    if on and _input_update not in handlers:handlers.append(_input_update)
    elif not on and _input_update in handlers:handlers.remove(_input_update)


def _start(context):
    from . import dress as DR, store, ahead_prepare
    session=DR._LIVE_SESSION
    if session is None or session.poisoned or session.lease is not None or session.process.poll() is not None:
        _skip('no_idle_solver');return
    spec=store.get(context)[0]
    if spec is None:_skip('no_pattern');return
    g=DR.garment(spec)
    if g is None or (not g.get('ac9_job') and not any(m.type=='MESH_CACHE' for m in g.modifiers)):
        _skip('no_result');return
    _A['skip']=None
    t=time.perf_counter();task=ahead_prepare.start(context,_A['generation'])
    task.session=session
    _A['prep']=task
    _watch_inputs(True)
    _trace('build_ahead_capture',generation=task.generation,spec_sha=task.spec_sha,snapshot_s=round(task.snapshot_s,6),
           main_s=round(time.perf_counter()-t,6),phases=task.capture_phases,bytes=(task.root/'scene.blend').stat().st_size)
    if not bpy.app.timers.is_registered(_poll):bpy.app.timers.register(_poll,first_interval=0.05,persistent=True)


def _prepared(context,task):
    from . import dress as DR
    if (task.generation!=_A['generation'] or not task.matches(context)
            or DR._LIVE_SESSION is not task.session):
        _skip('stale_preparation');_abort_prep();return
    if not enabled(context):
        _skip('not_enabled');_abort_prep();return
    if _busy_retry():return
    session=DR._LIVE_SESSION
    if session is None or session.poisoned or session.lease is not None or session.process.poll() is not None:
        _skip('no_idle_solver');_abort_prep();return
    t=time.perf_counter()
    try:
        row=task.read();key=row['key']
        if key==_A['failed']:_skip('failed_before');_abort_prep();return
        python=DR.newton_python(context)
        env=DR.solver_process_env(python,row['solver_env']) if python else None
        if env is None or not session.compatible(python,env,row['cfg']):
            _skip('incompatible');_abort_prep();return
        timing=dict(prepare_s=row['prepare_s'],key_s=row['key_s'])
        metrics=dict(edit_generation=task.generation,prepare_s=round(row['prepare_s'],6),
                     worker_s=round(row['worker_s'],6),phases=row['phases'],snapshot_s=round(task.snapshot_s,6))
        job=session.submit(row['idx'],row['avatar'],row['cfg'],row['stage'],op='build')
    except Exception as exc:
        _trace('build_ahead_error',error=repr(exc)[:500]);_abort_prep();return
    # The resident now owns a lease: retain the private input even if diagnostics fail.
    _A.update(job=job,key=key,stop_t=None,wait_t=None,t_sent=time.monotonic(),timing=timing)
    try:_trace('build_ahead_sent',**metrics,main_s=round(time.perf_counter()-t,6),generation=job.command['generation'])
    except Exception:pass


def _sweep(base):
    import shutil
    try:
        names = os.listdir(base)
    except OSError:
        return
    for n in names:
        shutil.rmtree(os.path.join(base, n), ignore_errors=True)


def _key(idx, avatar, cfg):
    try:
        from .solver import resident_runner as RR
    except Exception:
        return None
    try:
        return RR.structure_key(idx, avatar, cfg)
    except Exception:
        return None


def _abort_prep():
    if hasattr(bpy.app,'handlers'):_watch_inputs(False)
    owner, _A['prep'] = _A['prep'], None
    if owner is not None:
        try:
            owner.abort()
        except Exception:
            pass


def _finish():
    """The build job answered (or its session went): free the lease and the temporary input."""
    job = _A['job']
    row = None
    try:
        row = json.loads(Path(job.receipt).read_text(encoding='utf8'))
    except (OSError, ValueError, AttributeError):
        pass
    audit = (row or {}).get('audit') or {}
    mode = (audit.get('resident') or {}).get('mode')
    if job.returncode or not mode:
        if not audit.get('cancelled'):
            _A['failed'] = _A['key']                # not asked again until the structure changes
    try:
        job.release()
    except ValueError:
        pass
    _A['last'] = dict(key=_A['key'], mode=mode, code=job.returncode, cancelled=bool(audit.get('cancelled')),
                      setup_s=(audit.get('resident') or {}).get('setup_s'), wall_s=round(time.monotonic() - _A['t_sent'], 3))
    _trace('build_ahead_done', mode=mode, code=job.returncode, cancelled=bool(audit.get('cancelled')),
           wall_s=_A['last']['wall_s'])
    _A.update(job=None, stop_t=None, wait_t=None)
    _abort_prep()


def _poll():
    job = _A['job']
    if job is None:
        task=_A['prep']
        if task is None:return None
        if not enabled() or task.generation!=_A['generation'] or not task.matches(bpy.context):
            _skip('stale_preparation');_abort_prep();return None
        if task.poll() is None:return 0.05
        _prepared(bpy.context,task)
        return 0.25 if _A['job'] is not None else (0.05 if _A['prep'] is not None else None)
    if job.poll() is None:
        return 0.25
    _finish()
    return None


def _stop_running():
    job = _A['job']
    if job is not None and job.poll() is None and _A['stop_t'] is None:
        try:
            Path(job.command['args'][3], 'stop').touch()
        except OSError:
            pass
        _A['stop_t'] = time.monotonic()
        _trace('build_ahead_stop')


def yield_to(idx, avatar, cfg_path):
    """A press found the waiting solver busy with a build. True: the lease is free now (or the session was ended
    and the press goes on as without one); False: try again shortly. Same structure: wait for it (faster than a
    fresh build). Another: stop it; not stopped within STOP_GIVE_UP_S, the session is ended."""
    from . import dress as DR
    job = _A['job']
    if job is None:
        return True
    if job.poll() is not None:
        _finish()
        return True
    if _A['press_key'] is None or _A['press_key'][0] != cfg_path:
        try:
            cfg = json.loads(Path(cfg_path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            cfg = None
        _A['press_key'] = (cfg_path, _key(idx, avatar, cfg) if cfg is not None else None)
    if _A['press_key'][1] is not None and _A['press_key'][1] == _A['key']:
        # the press's own structure: it will begin on it. A build that does not answer in twice a build's time
        # (stuck) is given up as below, and the press starts from scratch.
        if _A['wait_t'] is None:
            _A['wait_t'] = time.monotonic()
        last = _A['last'] or {}
        limit = 2.0 * (last.get('wall_s') if last.get('mode') == 'build' and last.get('wall_s') else FRESH_GUESS_S)
        if time.monotonic() - _A['wait_t'] <= limit:
            return False
        _trace('build_ahead_wait_gave_up', waited_s=round(time.monotonic() - _A['wait_t'], 3))
        return _give_up(DR)
    _stop_running()
    if time.monotonic() - _A['stop_t'] > STOP_GIVE_UP_S:
        _trace('build_ahead_stop_gave_up')
        return _give_up(DR)
    return False


def _give_up(DR):
    DR._session_end()
    _A.update(job=None, stop_t=None, wait_t=None)
    _abort_prep()
    return True


def end():
    """Unregister / another file: stop a build in flight and drop its temporary input."""
    _A['generation']+=1
    _stop_running()
    if _A['job'] is None:_abort_prep()
    elif hasattr(bpy.app,'handlers'):_watch_inputs(False)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _A.update(due=0.0, press_key=None, wait_t=None)
