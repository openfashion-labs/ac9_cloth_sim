# SPDX-License-Identifier: GPL-3.0-or-later
"""The .blend is the whole result; a run's folder is a cache.

A dressed garment keeps in its own object what the next run needs and that used to be in its result folder only:
the last stage's solver config (what Continue and Live take over, CONTINUE_INHERIT) and the pattern it was dressed
with (ac9_spec.json). A folder that is gone -- deleted, the .blend copied alone, emptied by the system's cleanup --
then costs the frames on the timeline only: the next run makes a folder from the garment (dress.job_from_mesh) and
reads these two from here.

The record says which result it is (ac9_record_key: the pieces and their vertex counts). It is used only while the
garment's mesh is still that result: a garment object used again for another result (an import of the same name)
never takes over the old pattern or config.

It also keeps the folders the garment's runs and pattern carries wrote (ac9_job_history), each with the .blend that
was open when it was written, so that the old ones are deleted when a newer one is there: the latest only per
garment, unless Keep Old Runs is on in the Preferences. Only folders written while this .blend was open are ever
deleted (several .blend files can share one AC9_ClothSim_jobs folder, and a file saved under a new name keeps the
older file's folders in its history), and never one any object reads, one the saved .blend on disk reads (until it
is saved again), or one still being copied.
An undo back to a deleted folder marks the garment as having lost it (dress._drop_missing_cache): it goes on from
its own shape, as a .blend copied alone does.
"""
import json
import os
import threading
import time

import bpy

from .core import old_runs as OR

CONFIG_PROP = 'ac9_last_config'        # json: the solver config of the last stage of the garment's last run
SPEC_PROP = 'ac9_dressed_spec'         # the pattern (spec text) the garment's mesh shows
HISTORY_PROP = 'ac9_job_history'       # json list of {path, file}: the folders written, and the .blend open then
KEY_PROP = 'ac9_record_key'            # which result the record is: the pieces and their vertex counts
JOBS_DIR = 'AC9_ClothSim_jobs'

# the folders the saved .blend on disk reads (set on load and on save): kept until the file is saved again, so that
# closing without saving finds the folders the file names
_SAVED_REFS = set()


def _canon(path):
    """The spelling a folder is compared by on Blender's main thread: from the string alone, never asking the disk
    (core/old_runs.spelled). Links are resolved on the archive's thread only (old_runs.canon)."""
    try:
        return OR.spelled(bpy.path.abspath(str(path)))
    except (OSError, ValueError):
        return ''


# ---- what the garment keeps --------------------------------------------------------------------------------------

def _key(names, counts):
    import hashlib
    return hashlib.sha1(json.dumps([[str(n) for n in names], [int(c) for c in counts]]).encode()).hexdigest()


def result_key(job):
    """The pieces of a result folder and their vertex counts, as a key; None when it cannot be read."""
    from . import result_reference as RR
    try:
        r = RR.open_result(job)
        rng = r.piece_ranges()
        return _key(r.names, [rng[n][1] - rng[n][0] for n in r.names])
    except Exception:                                 # noqa: BLE001
        return None


def mesh_key(g):
    """The same key from g's mesh (its piece names and the piece of each face); None when it has none."""
    import numpy as np
    try:
        names = json.loads(g.get('ac9_piece_names') or '[]')
        me = g.data
        a = me.attributes.get('ac9_piece')
        if not names or a is None or a.domain != 'FACE' or not len(me.polygons):
            return None
        fp = np.empty(len(me.polygons), np.int32)
        a.data.foreach_get('value', fp)
        lt = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get('loop_total', lt)
        lv = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get('vertex_index', lv)
        vp = np.full(len(me.vertices), -1, np.int64)
        vp[lv] = np.repeat(fp, lt)
        if (vp < 0).any() or vp.max() >= len(names):
            return None
        return _key(names, np.bincount(vp, minlength=len(names)))
    except Exception:                                 # noqa: BLE001
        return None


def _set_key(g, job):
    k = result_key(job)
    if k:
        g[KEY_PROP] = k
    else:
        forget(g)


def remember_run(g, job):
    """A run's result was accepted into g: keep its last stage's config and its pattern on g, as job's record."""
    if g is None or not job:
        return
    from . import dress as DR
    from . import result_reference as RR
    try:
        forget(g)
        cfg = DR._last_stage_config(job)
        if cfg:
            g[CONFIG_PROP] = json.dumps(cfg, ensure_ascii=False, separators=(',', ':'))
        text = RR.open_result(job).dressed_spec_text()
        if text:
            g[SPEC_PROP] = text
        _set_key(g, job)
    except Exception as exc:                          # noqa: BLE001 -- a result is never lost over its record
        print("AC9 Cloth Sim: the run's record was not kept in the .blend:", exc)
    note(g, job)


def remember_spec(g, text, job, keep_config=True):
    """A pattern edit carried to g, written to job: the pattern g shows now. The config stays when it was the record
    of the result carried from (a carry goes on with that run); the record describes job's result from now on."""
    if g is None or not text:
        return
    if not keep_config:
        forget(g)
    g[SPEC_PROP] = text
    _set_key(g, job)


def forget(g):
    """g shows another result than the recorded one (an import into a garment object of the same name)."""
    if g is not None and getattr(g, 'is_editable', True):
        for k in (CONFIG_PROP, SPEC_PROP, KEY_PROP):
            if k in g:
                del g[k]


def record_valid(g):
    """The record is the result g's mesh shows: the same pieces with the same vertex counts."""
    k = g.get(KEY_PROP) if g is not None else None
    return bool(k) and k == mesh_key(g)


def last_config(g):
    """The last run's solver config kept on g, or {} (none, or not of the result g shows)."""
    if g is None or not g.get(CONFIG_PROP) or not record_valid(g):
        return {}
    try:
        cfg = json.loads(g.get(CONFIG_PROP))
    except (TypeError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def dressed_spec_text(g):
    """The pattern g was dressed with, or None (none, or not of the result g shows)."""
    text = g.get(SPEC_PROP) if g is not None else None
    return str(text) if text and record_valid(g) else None


# ---- the folders a garment wrote ---------------------------------------------------------------------------------

def _entries(g):
    """[{'path', 'file'}]: a folder and the .blend open when it was written ('' unsaved, None not known)."""
    try:
        h = json.loads(g.get(HISTORY_PROP) or '[]')
    except (TypeError, ValueError):
        return []
    out = []
    for e in (h if isinstance(h, list) else []):
        if isinstance(e, dict) and e.get('path'):
            out.append({'path': str(e['path']), 'file': e.get('file')})
        elif isinstance(e, str) and e:
            out.append({'path': e, 'file': None})
    return out


def history(g):
    return [e['path'] for e in _entries(g)]


def _this_file():
    return _canon(bpy.data.filepath) if bpy.data.filepath else ''


def note(g, *paths):
    """Add folders g's runs or carries wrote to its history, with the .blend open now. Strings only: a run's Stop
    calls this, and the folder beside the .blend can be on a disk busy renaming a copy there (a check of a path in
    it waited up to 17 s, measured on a USB hard disk)."""
    if g is None or not getattr(g, 'is_editable', True):
        return
    h = _entries(g)
    seen = {_canon(e['path']) for e in h}
    for p in paths:
        if not p:
            continue
        p = os.path.normpath(os.path.abspath(bpy.path.abspath(str(p))))
        if _canon(p) not in seen:
            h.append({'path': p, 'file': _this_file()})
            seen.add(_canon(p))
    g[HISTORY_PROP] = json.dumps(h, ensure_ascii=False)


def _temp_roots():
    """The AC9_ClothSim_jobs folders of no .blend: the system temp folder, Live's working and kept copies."""
    import tempfile
    out = {_canon(os.path.join(tempfile.gettempdir(), JOBS_DIR))}
    try:
        from . import job_archive as JA
        out.add(_canon(os.path.join(str(JA.working_root(bpy.context)), JOBS_DIR)))
        out.add(_canon(str(JA._recovery_root(bpy.context))))
    except Exception:                                 # noqa: BLE001
        pass
    return out


def _roots():
    """Where this file's runs write: beside it, Live's working and kept copies, the system temp folder (unsaved).
    A folder elsewhere -- beside another .blend this one was saved from -- is that file's, never deleted here."""
    out = _temp_roots()
    if bpy.data.filepath:
        out.add(_canon(os.path.join(os.path.dirname(bpy.data.filepath), JOBS_DIR)))
    return out


def garments():
    from . import dress as DR
    return [o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith(DR.GARMENT_PREFIX)
            and getattr(o, 'is_editable', True)]


def _current(g):
    """The folders g reads now (its stored result, its Mesh Cache's folder), as strings."""
    out = []
    if g.get('ac9_job'):
        out.append(bpy.path.abspath(str(g.get('ac9_job')), library=g.library))
    for m in g.modifiers:
        if m.type == 'MESH_CACHE' and m.filepath:
            out.append(os.path.dirname(bpy.path.abspath(m.filepath, library=g.library)))
    return out


def _busy():
    try:
        from . import job_archive as JA
        return sorted(JA.busy_paths())
    except Exception:                                 # noqa: BLE001
        return []


def snapshot(objects=None):
    """What the old runs are judged from (core/old_runs), read from Blender on the main thread without a call to
    the file system: the judgement and the deletion run on the archive's thread (job_archive.request_sweep)."""
    readers = []
    for ob in bpy.data.objects:
        if ob.type == 'MESH':
            readers += _current(ob)
    return dict(this_file=_this_file(), roots=sorted(_roots()), temp_roots=sorted(_temp_roots()),
                saved=sorted(_SAVED_REFS), readers=readers, busy=_busy(),
                garments=[dict(name=g.name, entries=_entries(g), current=_current(g))
                          for g in (objects if objects is not None else garments())])


def apply_sweep(result):
    """On the main thread, a clean-up done: drop the deleted folders and the ones no longer there from the
    garments' histories (by the strings they were stored as)."""
    drop = {}
    for key in ('gone', 'missing'):
        for name, paths in (result.get(key) or {}).items():
            drop.setdefault(name, set()).update(paths)
    for g in garments():
        if g.name not in drop:
            continue
        h = _entries(g)
        keep = [e for e in h if e['path'] not in drop[g.name]]
        if keep != h:
            g[HISTORY_PROP] = json.dumps(keep, ensure_ascii=False)
    if result.get('deleted'):
        _USAGE['stamp'] = 0.0


def old_runs(objects=None):
    """[(garment, folder)] -- the folders that are old (core/old_runs.judge). Reads the disk: never on a Stop."""
    by = {g.name: g for g in (objects if objects is not None else garments())}
    return [(by[n], p) for n, p in OR.judge(snapshot(objects)) if n in by]


def clean_up(objects=None):
    """The Clean Up button: delete the old folders now and drop the gone ones from the garments' histories.
    Returns (folders deleted, bytes freed). A press may wait for the disk; a run's Stop never calls this."""
    out = OR.sweep(snapshot(objects))
    apply_sweep(out)
    return out['deleted'], out['freed']


def keep_old_runs(context=None):
    from . import dress as DR
    prefs = DR.addon_prefs(context or bpy.context)
    return bool(getattr(prefs, 'keep_old_runs', False)) if prefs is not None else False


def auto_clean(g, context=None):
    """After a run or a carry made a new result for g: its old folders go, unless Keep Old Runs is on. Queued to
    the archive's thread (after the copy beside the .blend queued before it); returns at once."""
    if g is None or keep_old_runs(context):
        return 0
    try:
        from . import job_archive as JA
        JA.request_sweep(snapshot([g]))
    except Exception as exc:                          # noqa: BLE001 -- never a run's failure
        print("AC9 Cloth Sim: old runs not cleaned up:", exc)
    return 0


# ---- the saved file's folders ------------------------------------------------------------------------------------

@bpy.app.handlers.persistent
def remember_saved(*_):
    """On load and after a save: the folders the file on disk reads, kept until it is saved again."""
    _remember_saved()


@bpy.app.handlers.persistent
def clean_on_save(*_):
    """After a save: the folders the file named before are free now; the old ones go (unless kept), on the
    archive's thread."""
    _remember_saved()
    if not keep_old_runs():
        try:
            from . import dress as DR
            from . import job_archive as JA
            if DR.AC9_OT_SimDress.running() is None:
                JA.request_sweep(snapshot())
        except Exception as exc:                      # noqa: BLE001 -- a save never fails over this
            print("AC9 Cloth Sim: old runs not cleaned up:", exc)


def _remember_saved():
    _SAVED_REFS.clear()
    for g in garments():
        for k in ('ac9_job', 'ac9_job_recovery'):
            if g.get(k):
                _SAVED_REFS.add(bpy.path.abspath(str(g.get(k)), library=g.library))
    _USAGE['stamp'] = 0.0


# ---- the use of the disk (drawn in the run's Settings) ----------------------------------------------------------

_USAGE = {'bytes': None, 'old': None, 'stamp': 0.0, 'thread': None, 'key': None}


def _folders():
    """Every folder of this .blend's garments, as strings (whether each is there is asked on the thread)."""
    out = []
    for g in garments():
        out += history(g) + _current(g)
    return out


def usage(refresh_after=5.0):
    """(MB of this .blend's folders, MB of the old ones) or (None, None) while first measured. Never touches the
    disk in a draw: the folders are judged and summed on a thread, at most every refresh_after seconds."""
    now = time.monotonic()
    t = _USAGE['thread']
    if (t is None or not t.is_alive()) and now - _USAGE['stamp'] > refresh_after:
        try:
            snap, allp = snapshot(), _folders()
        except Exception:                             # noqa: BLE001
            snap, allp = {}, []
        _USAGE['stamp'] = now

        def work(snap=snap, allp=allp):
            try:
                _USAGE['bytes'], _USAGE['old'] = OR.usage(snap, allp)
            except Exception:                         # noqa: BLE001
                pass
        _USAGE['thread'] = threading.Thread(target=work, daemon=True)
        _USAGE['thread'].start()
    b, o = _USAGE['bytes'], _USAGE['old']
    return (None, None) if b is None else (b / 1e6, (o or 0) / 1e6)


_HANDLERS = (('load_post', 'remember_saved'), ('save_post', 'clean_on_save'))


def register():
    unregister()
    for where, name in _HANDLERS:
        getattr(bpy.app.handlers, where).append(globals()[name])


def unregister():
    for where, name in _HANDLERS:
        hs = getattr(bpy.app.handlers, where)
        for h in [h for h in hs if getattr(h, '__name__', '') == name]:
            hs.remove(h)
