# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread job references; background writer owns no Blender data."""
import os
from pathlib import Path
import tempfile
import time

import bpy

from .core import job_workspace as W
from .solver.lifecycle_trace import mark

WORK_LABEL = 'Simulation Working Directory'
# what the folder really holds (blend_record, user 2026-11-22): Simulate's working copy and the copy kept until the
# result is beside the saved .blend; the results themselves go beside the .blend
WORK_HELP = ("Where Simulate writes while it runs, and the copy kept until its result is beside the saved .blend. "
             "Empty: Blender's and the system's temporary folders")

if '_WRITER' not in globals():
    _WRITER = W.ArchiveQueue()
    _TARGETS = {}
    _NOTICES = {}
    _EPOCH = 0
if '_DEFERRED_EXPORTS' not in globals():
    _DEFERRED_EXPORTS = {}
if '_DEFERRED_TASKS' not in globals():
    _DEFERRED_TASKS = []
if '_CANCELLED' not in globals():
    _CANCELLED = set()                 # recoveries whose export is no longer wanted (a newer result of the garment)
    _QUIET = {'since': 0.0}            # when the last result was accepted

# A Live result is copied beside the .blend only once no Live has run for QUIET_S seconds, and only the garment's
# newest: a Live started again within seconds made the copy of the one before pointless, and the copy's writes on a
# slow disk (a USB hard disk) ran under the next Live. Saving the .blend copies at once.
QUIET_S = 3.0


def working_root(context):
    entry = context.preferences.addons.get(__package__)
    preference = getattr(entry.preferences, 'simulation_work_dir', '') if entry else ''
    value = bpy.path.abspath(preference) if preference else ''
    return W.work_root(value, bpy.app.tempdir or tempfile.gettempdir())


def worker_temp(context):
    root = working_root(context) / 'tmp'
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _recovery_root(context):
    entry = context.preferences.addons.get(__package__)
    preference = getattr(entry.preferences, 'simulation_work_dir', '') if entry else ''
    # Completed results must survive deletion of Blender's per-process temp dir.
    base = Path(bpy.path.abspath(preference)) if preference else Path(tempfile.gettempdir())
    return base.resolve() / 'AC9_ClothSim_recovery' / 'AC9_ClothSim_jobs'


def remember(context, directory):
    path = str(Path(directory).resolve())
    destination = (Path(bpy.data.filepath).parent / 'AC9_ClothSim_jobs' / Path(path).name
                   if bpy.data.filepath else None)
    _TARGETS[path] = dict(working=path, recovery=str(_recovery_root(context) / Path(path).name),
                          target=str(destination) if destination else None,
                          blend=bpy.data.filepath)


def recovery_path(directory):
    return _TARGETS.get(str(Path(directory).resolve()), {}).get('recovery')


def busy_paths():
    """The folders a Live result's copy is being made from or to now (never deleted by the clean-up)."""
    out = set()
    for plan in _TARGETS.values():
        out |= {p for p in (plan.get('working'), plan.get('recovery'), plan.get('target')) if p}
    for (recovery, _generation), target in _DEFERRED_EXPORTS.items():
        out |= {recovery, target}
    out |= set(_NOTICES)
    with _WRITER.lock:
        for key in _WRITER.pending:
            if key and key[0] == 'task':
                continue                     # a clean-up, not a folder
            out |= {k for k in key[1:2] if isinstance(k, str)}
            dest = _WRITER._destinations.get(key)
            if dest:
                out.add(dest)
    return out


def accept_result(obj, directory, lease):
    """Save the accepted result's identity even while its writer is releasing.

    The recovery destination is only a promise here. Loading still requires the
    sealed generation and the bound archive plan before adopting that result.
    """
    recovery = recovery_path(directory)
    if recovery and lease:
        obj['ac9_job_recovery'] = recovery
        obj['ac9_job_generation'] = lease['token']
    obj['ac9_job'] = directory


def capture(directory, object_name):
    """Freeze acceptance/ownership while the GUI still owns this run."""
    source = str(Path(directory).resolve())
    obj = bpy.data.objects.get(object_name)
    plan = _TARGETS.get(source)
    if not plan or obj is None or obj.get('ac9_job') != directory:
        return None
    return dict(job=source, plan=dict(working=source, **plan) if 'working' not in plan else dict(plan),
                epoch=_EPOCH, object_uid=obj.session_uid, object_name=object_name)


def finish(context, directory, object_name, lease, *, request=None):
    """Caller supplies the completed job lease and its finished GUI consumer.

    Recovery is committed locally before accepting Stop. Export to the .blend's
    neighbouring job folder is asynchronous. Never use the current filename later.
    """
    source = str(Path(directory).resolve())
    request = request if request is not None else capture(directory, object_name)
    if request is None:
        # An unaccepted first frame/Undo restored the preceding result.
        _TARGETS.pop(source, None)
        return None
    if request['job'] != source:
        raise ValueError('The accepted archive request belongs to another job')
    plan = request['plan']
    obj = bpy.data.objects.get(request['object_name'])
    apply = (request['epoch'] == _EPOCH and obj is not None
             and obj.session_uid == request['object_uid'] and obj.get('ac9_job') == directory)
    W.require_released(source, lease)
    W._write(Path(source)/W.PLAN, dict(version=1, generation=lease['token'], **plan))
    mark('archive_seal_begin', job=source)
    generation = W.seal_finished(source, lease)
    mark('archive_sealed', job=source)
    recovery = W.archive_finished(source, plan['recovery'])
    mark('archive_recovery_ready', job=source, recovery=recovery)
    # This synchronous transition runs before the operator makes Stop available.
    if apply:
        obj['ac9_job'] = recovery
        obj['ac9_job_recovery'] = recovery
        obj['ac9_job_generation'] = generation
        from . import blend_record as BR
        BR.note(obj, recovery)
    if plan['target']:
        _submit(recovery, plan['target'], obj if apply else None, generation)
    _TARGETS.pop(source, None)
    mark('archive_queued', job=source)
    return recovery


def _submit(recovery, target, obj, generation):
    if obj is not None:
        _supersede(obj.session_uid, recovery)
        _NOTICES[recovery] = dict(epoch=_EPOCH, object_uid=obj.session_uid,
                                 expected=recovery, generation=generation)
    _DEFERRED_EXPORTS[(recovery,generation)]=target
    _QUIET['since'] = time.monotonic()
    _drain_submissions()
    if not bpy.app.timers.is_registered(collect):
        bpy.app.timers.register(collect, first_interval=0.1, persistent=True)


def _supersede(object_uid, recovery):
    """A newer result of the garment: the copies of its older ones are not made (not queued yet: dropped; under way:
    stopped between files). Their recovery copies stay in its history and go as old runs."""
    for old, notice in list(_NOTICES.items()):
        if notice['object_uid'] != object_uid or old == recovery:
            continue
        waiting = [k for k in _DEFERRED_EXPORTS if k[0] == old]
        for k in waiting:
            del _DEFERRED_EXPORTS[k]
        if waiting:
            _NOTICES.pop(old, None)                      # never queued: no result will come
        else:
            _CANCELLED.add(old)                          # queued or being copied: its result clears it
        mark('archive_superseded', job=old, queued=not waiting)


def _cancelled(recovery):
    return recovery in _CANCELLED


def _live_running():
    try:
        from .dress import AC9_OT_SimDress
        return AC9_OT_SimDress.running() is not None
    except Exception:                                    # noqa: BLE001 -- not loaded (tests): nothing runs
        return False


def _drain_submissions(force=False):
    """An F3 reload retains the previous writer until all its results are read. The copies wait for QUIET_S
    seconds without a Live (force: a save, now)."""
    global _WRITER
    if not hasattr(_WRITER,'submit_accepted') or (_DEFERRED_TASKS and not hasattr(_WRITER,'submit_task')):
        if _WRITER.pending or not _WRITER.results.empty():
            return
        _WRITER=W.ArchiveQueue()
    _WRITER.cancelled = _cancelled
    if _DEFERRED_EXPORTS and (force or (time.monotonic() - _QUIET['since'] >= QUIET_S and not _live_running())):
        for (recovery,generation),target in list(_DEFERRED_EXPORTS.items()):
            _WRITER.submit_accepted(recovery,target,generation)
            del _DEFERRED_EXPORTS[(recovery,generation)]
            mark('archive_export_queued', job=recovery, forced=force)
    while _DEFERRED_TASKS:
        kind, work = _DEFERRED_TASKS.pop(0)
        _WRITER.submit_task(kind, work)


def _still_free(epoch):
    """For the clean-up on the writer's thread: right before a folder is deleted, the .blend was not undone or
    reloaded since the snapshot and the folder is not being written. In doubt, it is kept."""
    from .core import old_runs as OR

    def free(path):
        if _EPOCH != epoch:
            return False
        try:
            busy = {OR.canon(p) for p in busy_paths()}
        except Exception:                             # noqa: BLE001 -- a dict changed on the main thread: keep it
            return False
        return OR.canon(path) not in busy
    return free


def request_sweep(snap):
    """Delete the old runs in snap (blend_record.snapshot) on the writer's thread, after the copies queued before
    it; the histories are updated by collect when it is done. Never waits."""
    from .core import old_runs as OR
    epoch = _EPOCH
    free = _still_free(epoch)

    def work(snap=snap, epoch=epoch):
        mark('old_runs_sweep_begin')
        out = OR.sweep(snap, free)
        mark('old_runs_sweep_end', deleted=out['deleted'])
        return dict(out, epoch=epoch)
    _DEFERRED_TASKS.append(('old_runs', work))
    _drain_submissions()
    if not bpy.app.timers.is_registered(collect):
        bpy.app.timers.register(collect, first_interval=0.1, persistent=True)


def resume(context):
    """Resume only the sealed export bound to this loaded object's generation."""
    import json
    for obj in bpy.data.objects:
        if not getattr(obj, 'is_editable', True):
            continue
        generation = obj.get('ac9_job_generation')
        recovery = obj.get('ac9_job_recovery') or obj.get('ac9_job')
        if (not isinstance(generation,str) or not isinstance(recovery,str) or not generation or not recovery
                or not W.saved_generation(recovery, generation)):
            continue
        try:
            plan = json.loads((Path(recovery)/W.PLAN).read_text(encoding='utf8'))
            target = Path(plan['target']) if plan.get('target') else None
            if (plan.get('version') != 1 or plan.get('generation') != generation
                    or str(Path(recovery).resolve()) != plan.get('recovery')
                    or (target is not None and (target.name != Path(recovery).name
                                               or target.parent.name != 'AC9_ClothSim_jobs'))):
                continue
            current = obj.get('ac9_job')
            if not isinstance(current, str) or str(Path(current).resolve()) not in (
                    plan.get('working'), plan.get('recovery'), str(target) if target is not None else None):
                continue
            current = Path(current).resolve()
            if target is not None and current == target.resolve() and current.exists():
                # A published, valid current result needs no rollback/re-export.
                if W.saved_generation(current, generation):
                    continue
                continue
            if str(current) == plan.get('working') and current.exists():
                seal = json.loads((current/W.SEAL).read_text(encoding='utf8'))
                if seal.get('token') != generation or seal.get('files') != W._inventory(current):
                    continue
        except (OSError, ValueError, TypeError, KeyError):
            continue
        # Loading reacquires the current object/epoch; an old callback alone
        # never gains this authority by finding the same name or path.
        obj['ac9_job'] = str(Path(recovery).resolve())
        if target is not None:
            _submit(str(Path(recovery).resolve()), str(target), obj, generation)


def collect():
    from .i18n import tx
    for result in _WRITER.collect():
        if 'task' in result:
            if result['task'] == 'old_runs':
                value = result.get('value') or {}
                if result.get('error'):
                    print("AC9 Cloth Sim: the old runs' clean-up failed:", result['error'])
                elif value.get('epoch') == _EPOCH:
                    from . import blend_record as BR
                    BR.apply_sweep(value)
            continue
        mark('archive_apply_begin', job=result['source'])
        expected = _NOTICES.pop(result['source'], None)
        _CANCELLED.discard(result['source'])
        if result.get('cancelled'):
            mark('archive_cancelled', job=result['source'])     # a newer result's copy follows; nothing was made
            continue
        if result['error']:
            # Local recovery remains authoritative and usable; never call a
            # failed export saved, or replace it with a partial directory.
            if expected and expected['epoch'] == _EPOCH:
                bpy.context.scene.ac9_cloth_sim.dress_status = tx(
                    'The result is kept locally; saving beside the blend failed: {0}', result['error'])
            continue
        if not expected or expected['epoch'] != _EPOCH:
            continue
        obj = next((ob for ob in bpy.data.objects if ob.session_uid == expected['object_uid']), None)
        if (obj is not None and obj.get('ac9_job') == expected['expected']
                and obj.get('ac9_job_generation') == expected['generation']):
            # Live retains its mesh, not a Mesh Cache. A cache-bearing object is
            # deliberately not switched by this Live-only writer.
            if not any(mod.type == 'MESH_CACHE' for mod in obj.modifiers):
                obj['ac9_job'] = result['target']
                # the copy beside the .blend is the result now: the working copy and the one in the temp folder are
                # old runs (the same files three times over), deleted unless old runs are kept
                from . import blend_record as BR
                BR.note(obj, result['target'])
                BR.auto_clean(obj)
        elif obj is not None and obj.get('ac9_job') != expected['expected']:
            # copied although the garment had moved on (the stop came too late): an old run beside the .blend,
            # in its history so that the clean-up deletes it (it used to stay there, in no history)
            from . import blend_record as BR
            BR.note(obj, result['target'])
            BR.auto_clean(obj)
        mark('archive_apply_complete', job=result['source'])
    _drain_submissions()
    return (0.1 if _WRITER.pending or not _WRITER.results.empty() or _DEFERRED_EXPORTS or _DEFERRED_TASKS
            else None)


@bpy.app.handlers.persistent
def flush_on_save(*_):
    """Once the .blend is saved, the waiting results are copied beside it at once rather than after the quiet
    moment. The saved file still names Live's kept copy (its result switches to the copy beside it only when the
    copy is done, collect), so this is for the folder beside the file, not for the file's correctness. After the
    save, not before it: the copy then never shares the disk with Blender writing the .blend."""
    if _DEFERRED_EXPORTS:
        _drain_submissions(force=True)


@bpy.app.handlers.persistent
def invalidate(*_):
    global _EPOCH
    _EPOCH += 1


def register():
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        for callback in list(handlers):
            if getattr(callback, '__module__', '') == __name__ and callback.__name__ == 'invalidate':
                handlers.remove(callback)
        handlers.append(invalidate)
    for saves in (bpy.app.handlers.save_pre, bpy.app.handlers.save_post):     # save_pre: an older version's
        for callback in list(saves):
            if getattr(callback, '__module__', '') == __name__ and callback.__name__ == 'flush_on_save':
                saves.remove(callback)
    bpy.app.handlers.save_post.append(flush_on_save)
    if ((_WRITER.pending or not _WRITER.results.empty() or _DEFERRED_EXPORTS or _DEFERRED_TASKS)
            and not bpy.app.timers.is_registered(collect)):
        bpy.app.timers.register(collect, first_interval=0.1, persistent=True)


def unregister():
    invalidate()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        for callback in list(handlers):
            if getattr(callback, '__module__', '') == __name__ and callback.__name__ == 'invalidate':
                handlers.remove(callback)
    for saves in (bpy.app.handlers.save_pre, bpy.app.handlers.save_post):
        for callback in list(saves):
            if getattr(callback, '__module__', '') == __name__ and callback.__name__ == 'flush_on_save':
                saves.remove(callback)
    if bpy.app.timers.is_registered(collect):
        bpy.app.timers.unregister(collect)
